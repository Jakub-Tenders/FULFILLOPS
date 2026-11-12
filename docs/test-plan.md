# FulFillOps test plan

Owner: Partner 2. Stack: Python 3.12, pytest, httpx, SQLAlchemy/psycopg, ruff.

## 1. Environment and safety

- All tests run against a dedicated, disposable database named `fulfillops_test`
  (PostgreSQL 16 in Docker Compose). Nothing runs against a live dataset.
- `scripts/reset_test_data.py` rebuilds the schema and seed data (KIT-100 = 7,
  PART-200 = 12, PACK-300 = 5) before every test. It refuses to run unless
  `APP_ENV` is `test` or `development`, the URL does not look like prod/live, and
  the database name ends in `_test`.
- Failure policy being tested: delivery is at-least-once (a job can be claimed again
  after a crash), but the business effect happens once: stock is reserved atomically
  when the API accepts the order, and completion is a guarded state change on the order.
  A failed (dead-lettered) order keeps its reserved stock for manual reconciliation;
  there is no automatic restock.

## 2. Test matrix by layer

| Layer | What it proves | File | Runs against |
|---|---|---|---|
| Contract / validation | liveness, invalid JSON, missing Idempotency-Key, bad quantity, unknown order = 404, body-size limit | `tests/test_api_contract.py` (6) | in-process API |
| Race (in-process) | same key x8 reserves once; 12 different keys for 5 units | `tests/test_races.py` (2) | in-process API, test DB |
| Race (cross-replica) | same key x8 across api (8080) and api2 (8081); 12 different keys for 5 units over HTTP, final stock 6 and 0 | `tests/test_races_replicas.py` (2) | full Compose stack |
| Worker state | claim timeout and retry delay bounds, stale cutoff | `tests/test_worker_state.py` (3) | in-process |
| Worker recovery | stale job at max attempts goes to `dead_letter`; stale job below max returns to `pending` | `tests/test_worker.py` (2) | in-process, test DB |
| Worker reliability | poison job, backoff growth, two workers, crash recovery | `tests/test_worker_reliability.py` (4) | in-process, test DB |
| Black-box | official checker: basic, `--concurrency`, `--stress` | `fulfillops_public_checker.py` | full Compose stack |
| Drills (scripts) | worker killed mid-job; poison job on the real stack | `scripts/drill_worker_crash.sh`, poison block below | full Compose stack |

Total: 17 in-process tests + 2 cross-replica tests.

## 3. Worker reliability cases

- **Poison job:** customer `POISON-ORDER` always fails. After `WORKER_MAX_ATTEMPTS` (3)
  the `work_items` row is `dead_letter` with `last_error = "Deterministic poison order"`,
  the order is `failed`, the reserved stock stays reserved, and no further claim happens.
- **Backoff:** retry delay is `2**(attempts-1)` seconds: about 1 s after attempt 1 and
  about 2 s after attempt 2 (capped at 60 s).
- **Two workers:** 20 pending jobs, 2 concurrent workers (`FOR UPDATE SKIP LOCKED`): no
  job is claimed twice and every job completes with `attempts = 1`.
- **Crash recovery:** a job claimed by a worker that died is returned to `pending` once
  its 30 s lease expires, claimed again, and completed once (`attempts = 2`). A job that
  keeps crashing workers is dead-lettered at the attempt limit instead of looping forever.

## 4. How to run

The in-process worker tests must run **without** the Compose workers, because the
containers poll the same database and would take the jobs the tests create. So there
are two phases:

```bash
# Phase 1: PostgreSQL only, in-process tests
docker compose up -d --wait postgres
python -m pytest --ignore=tests/test_races_replicas.py -v

# Phase 2: full stack (2 APIs, 2 workers), replica tests and checker
docker compose up -d --build --wait --scale worker=2
python -m pytest tests/test_races_replicas.py -v
export APP_ENV=test DATABASE_URL=postgresql+psycopg://fulfillops:fulfillops@localhost:5432/fulfillops_test
python scripts/reset_test_data.py && python fulfillops_public_checker.py
python scripts/reset_test_data.py && python fulfillops_public_checker.py --concurrency
python scripts/reset_test_data.py && python fulfillops_public_checker.py --stress

# Crash drill (kills one worker mid-job)
./scripts/drill_worker_crash.sh
```

## 5. CI mapping

GitHub Actions (`.github/workflows/ci.yml`) runs the same two phases, plus:

| Step | Purpose |
|---|---|
| ruff | lint (`ruff.toml` excludes the two instructor-supplied files) |
| phase 1 / phase 2 pytest | unit, contract, race, worker and replica tests |
| checker (basic, concurrency, stress) | black-box contract, DB reset before each |
| Docker build + Trivy | fails on fixable HIGH/CRITICAL findings |
| SBOM (SPDX) | uploaded as an artifact on every run |

## 6. Evidence index

| Evidence | File |
|---|---|
| Cross-replica race tests | `docs/evidence/race_replicas.txt` |
| In-process worker and contract tests (17 passed) | `docs/evidence/worker_tests.txt` |
| Worker killed mid-job, order completed once | `docs/evidence/crash_recovery.txt` |
| Poison job reaches `dead_letter`, stock kept | `docs/evidence/poison_job.txt` |
| CI runs (red Trivy scan, green fix) | `docs/evidence/ci-evidence/ci-runs.md` |
| Trivy findings of the red run | `docs/evidence/ci-evidence/trivy_red.txt` |
| SBOM (SPDX JSON) | `docs/evidence/ci-evidence/sbom.spdx.json` |

## 7. Known limits

- The cross-replica test uses two API containers sharing one PostgreSQL, not two
  separate hosts.
- The in-process tests and the Compose workers cannot share one database at the same
  time (see section 4).
- Process-level crash recovery is shown with `docker kill` (SIGKILL), not node failure.