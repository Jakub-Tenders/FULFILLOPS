# FulfillOps test plan (Python)

Owner: Partner 2. Stack: Python 3.12, pytest, httpx, psycopg 3, ruff.
All tests run against an **isolated test DB** only (see "Safety").

## 1. Test matrix by layer

| Layer | What it covers | Where | Needs |
|---|---|---|---|
| unit | validation (qty, SKU, customer_id, key format), duplicate-SKU aggregation, total_cents, payload normalization | `tests/unit/` | nothing |
| integration | order -> job -> worker -> `completed`, replay returns same order_id, stock decremented at acceptance | `tests/integration/` | Postgres + API + worker |
| race | 8 same key, different keys vs last N units, multi-line aggregation | `tests/race/` | Postgres + 2 API instances |
| worker | 2 workers claim, crash mid-job, poison job, backoff, no double completion | `tests/worker/` | Postgres + worker(s) |
| black-box | `fulfillops_public_checker.py --concurrency --stress` | CI + manual | full stack, fresh DB |
| ops (scripts, not pytest) | kill worker in k8s, broken rollout, 30 s probe, p95 | `scripts/` | kind + Argo CD |

## 2. Tooling

```
requirements-dev.txt
pytest
pytest-timeout
httpx
psycopg[binary]
ruff
```

Commands:

```bash
ruff check . && ruff format --check .
pytest tests/unit -q
docker compose up -d --build
pytest tests/integration tests/race tests/worker -q --timeout=60
python fulfillops_public_checker.py --base-url http://localhost:8080 --concurrency --stress
```

## 3. Safety

- `scripts/reset_test_data.py` refuses to run unless `APP_ENV=test` and the DB name ends with `_test`.
- Race/worker tests call it in a session fixture. Never point them at anything else.

## 4. Shared fixtures (`tests/conftest.py`)

```python
import os, uuid, pytest, httpx, psycopg

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
BASE_URL_2 = os.getenv("BASE_URL_2", "http://localhost:8081")  # 2nd API replica
DB_URL = os.environ["DATABASE_URL"]

@pytest.fixture(autouse=True)
def reset_db():
    # safe reset: seeds 7 KIT-100, 12 PART-200, 5 PACK-300
    import subprocess, sys
    subprocess.run([sys.executable, "scripts/reset_test_data.py"], check=True)

@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        yield c

@pytest.fixture
def db():
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        yield conn

def new_key():
    return f"test-{uuid.uuid4()}"

def order(sku="PACK-300", qty=1, customer="CUST-T"):
    return {"customer_id": customer, "items": [{"sku": sku, "quantity": qty}]}

def stock(client, sku):
    return client.get(f"/api/v1/inventory/{sku}").json()["available"]
```

## 5. Race tests (the two required distinct ones)

### 5.1 Same key x8 across 2 replicas

Expected: one order_id, stock decremented once, all responses 202.

```python
from concurrent.futures import ThreadPoolExecutor
import httpx
from tests.conftest import BASE_URL, BASE_URL_2, order, new_key

def test_same_key_8_concurrent_two_replicas(client):
    key = new_key()
    urls = [BASE_URL, BASE_URL_2] * 4

    def post(url):
        with httpx.Client(base_url=url, timeout=10) as c:
            return c.post("/api/v1/orders", json=order("KIT-100", 1),
                          headers={"Idempotency-Key": key})

    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(post, urls))

    assert all(r.status_code == 202 for r in rs)
    assert len({r.json()["order_id"] for r in rs}) == 1
    assert client.get("/api/v1/inventory/KIT-100").json()["available"] == 6
```

### 5.2 Different keys vs last N units

Expected: exactly 5 accepted, 7 rejected `OUT_OF_STOCK`, stock = 0, never negative.

```python
def test_different_keys_last_units(client):
    def post(i):
        return client.post("/api/v1/orders", json=order("PACK-300", 1),
                           headers={"Idempotency-Key": new_key()})
    with ThreadPoolExecutor(12) as ex:
        rs = list(ex.map(post, range(12)))
    ok = [r for r in rs if r.status_code == 202]
    ko = [r for r in rs if r.status_code == 409]
    assert len(ok) == 5 and len(ko) == 7
    assert all(r.json()["error"]["code"] == "OUT_OF_STOCK" for r in ko)
    assert client.get("/api/v1/inventory/PACK-300").json()["available"] == 0
```

### 5.3 Extra cases

- duplicate SKU lines (`KIT-100` x2 lines of qty 4) -> 8 > 7 stock -> 409, nothing reserved (no partial allocation)
- same key, different payload -> 409 `IDEMPOTENCY_CONFLICT`
- same key, reordered JSON keys -> 202, same order_id
- replay after API restart (`docker compose restart api`) -> same order_id, stock unchanged

## 6. Worker tests

Assumes (to confirm with Partner 1): `jobs` table with `status, attempts, max_attempts, next_run_at, locked_by, locked_until, last_error`; worker env `WORKER_PROCESS_DELAY` (sleep per job, for crash tests) and a deterministic poison trigger (e.g. `customer_id` starting with `POISON-`, enabled only when `APP_ENV=test`).

| Test | Steps | Pass condition |
|---|---|---|
| two workers, no double completion | start 2 workers, create 20 orders | all `completed`, each order has exactly 1 completion, no job claimed by 2 workers at once |
| crash recovery | `WORKER_PROCESS_DELAY=10`, create order, `docker kill` worker holding it, keep the other | order still exists, lease expires, order ends `completed` exactly once, `attempts` = 2 |
| poison job | create `POISON-*` order | attempts reach `max_attempts`, job `dead`, order `failed`, stock **not** restored, no more retries after |
| backoff | poison job, read `next_run_at` per attempt | gaps grow (e.g. 2s, 4s, 8s) |
| retry metric | after poison job | `fulfillops_worker_retries_total` increased |

Crash test sketch:

```python
import subprocess, time

def test_worker_crash_recovery(client, db):
    r = client.post("/api/v1/orders", json=order("KIT-100", 1),
                    headers={"Idempotency-Key": new_key()})
    oid = r.json()["order_id"]
    # wait until a worker holds it
    for _ in range(50):
        row = db.execute("select locked_by from jobs where order_id=%s", (oid,)).fetchone()
        if row and row[0]:
            break
        time.sleep(0.2)
    subprocess.run(["docker", "kill", row[0]], check=True)  # locked_by = container name
    for _ in range(60):
        s = client.get(f"/api/v1/orders/{oid}").json()["status"]
        if s == "completed":
            break
        time.sleep(1)
    assert s == "completed"
    n = db.execute("select count(*) from job_events where order_id=%s and event='completed'", (oid,)).fetchone()[0]
    assert n == 1  # or equivalent audit check
```

(`locked_by` should be set to the container hostname so the test can kill the right one.)

## 7. Red-to-green (deliberate bug)

Bug to introduce on a branch: do the stock check with a plain `SELECT` **before** the transaction, then `UPDATE` without `WHERE available >= qty` (check-then-act).

1. Branch `bug/race-stock`, introduce the bug.
2. Run `test_different_keys_last_units` -> fails (oversell / negative stock). Save the log. This is the red PR in CI.
3. Fix with the atomic `UPDATE inventory SET available = available - $q WHERE sku=$s AND available >= $q` inside the transaction.
4. Rerun -> green. Save the log. This is the green PR.

Evidence to keep: the red CI run URL, the green CI run URL, the diff.

## 8. CI mapping

| CI step | Test layer |
|---|---|
| ruff | lint |
| pytest unit | unit |
| compose up + pytest integration/race/worker | integration, race, worker |
| public checker | black-box |
| trivy (blocking HIGH/CRITICAL, informational others) | supply chain |
| syft SBOM artifact | supply chain |

## 9. Ops drills (scripts)

- `scripts/drill_worker_kill.sh`: kill a worker pod mid-job, show before/during/after, order survives
- `scripts/drill_bad_rollout.sh`: bad image tag/readiness in Git, show rollout stuck, `git revert`, Argo CD back to Synced/Healthy
- `scripts/measure_p95.py`: 200+ requests, compute p95, save raw CSV in `docs/`
- `fulfillops_rollout_probe.py --duration 30 --interval 0.25 --output docs/rolling_update_probe.csv` during a good rolling update

## 10. Open points to align with Partner 1

- poison trigger and `WORKER_PROCESS_DELAY` hooks in the worker
- audit table/column to prove "completed exactly once"
- port for the 2nd API replica in Compose (8081)