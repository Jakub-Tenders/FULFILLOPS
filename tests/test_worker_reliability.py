import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text

from api.config import get_settings
from api.db import SessionLocal
from api.models import Order, WorkItem
from worker.main import claim_work, process_work

MAX_ATTEMPTS = get_settings().worker_max_attempts


def make_job(
    customer_id: str = "WORKER-TEST",
    order_status: str = "queued",
    job_status: str = "pending",
    attempts: int = 0,
    claimed_seconds_ago: int | None = None,
):
    order_id = uuid4()
    claimed_at = (
        datetime.now(timezone.utc) - timedelta(seconds=claimed_seconds_ago)
        if claimed_seconds_ago is not None
        else None
    )
    with SessionLocal.begin() as db:
        db.add(
            Order(order_id=order_id, customer_id=customer_id, status=order_status, total_cents=1299)
        )
        db.flush()
        db.add(
            WorkItem(
                order_id=order_id, status=job_status, attempts=attempts, claimed_at=claimed_at
            )
        )
    return order_id


def run_once() -> int | None:
    """One iteration of the worker loop (claim, then process). Returns the work_id or None."""
    with SessionLocal() as db:
        work_id = claim_work(db)
    if work_id is not None:
        process_work(work_id)
    return work_id


def fast_forward(order_id) -> None:
    """Skip the backoff wait so tests do not sleep."""
    with SessionLocal.begin() as db:
        db.execute(
            text("UPDATE work_items SET available_at = now() - interval '1 second' WHERE order_id = :o"),
            {"o": order_id},
        )


def job_and_order(order_id):
    with SessionLocal() as db:
        job = db.scalar(select(WorkItem).where(WorkItem.order_id == order_id))
        order = db.get(Order, order_id)
        return job, order


def seconds_until_available(order_id) -> float:
    with SessionLocal() as db:
        return float(
            db.scalar(
                text(
                    "SELECT extract(epoch from available_at - now()) FROM work_items WHERE order_id = :o"
                ),
                {"o": order_id},
            )
        )


def test_poison_job_dead_letters_after_max_attempts_and_keeps_stock(client):
    response = client.post(
        "/api/v1/orders",
        headers={"Idempotency-Key": "poison-test-0001"},
        json={"customer_id": "POISON-ORDER", "items": [{"sku": "PACK-300", "quantity": 1}]},
    )
    assert response.status_code == 202, response.text
    order_id = response.json()["order_id"]
    assert client.get("/api/v1/inventory/PACK-300").json()["available"] == 4

    for expected_attempt in range(1, MAX_ATTEMPTS + 1):
        assert run_once() is not None
        job, _ = job_and_order(order_id)
        assert job.attempts == expected_attempt
        fast_forward(order_id)

    job, order = job_and_order(order_id)
    assert job.status == "dead_letter"
    assert job.attempts == MAX_ATTEMPTS
    assert job.last_error == "Deterministic poison order"
    assert order.status == "failed"
    assert client.get(f"/api/v1/orders/{order_id}").json()["status"] == "failed"
    # reserved stock is retained for manual reconciliation: no automatic restock
    assert client.get("/api/v1/inventory/PACK-300").json()["available"] == 4
    # bounded: nothing left to claim, no infinite retry loop
    assert run_once() is None
    job, _ = job_and_order(order_id)
    assert job.attempts == MAX_ATTEMPTS


def test_retry_backoff_grows_between_attempts():
    order_id = make_job(customer_id="POISON-ORDER")

    run_once()  # attempt 1 fails, retry delay 2**0 = 1 s
    first = seconds_until_available(order_id)
    fast_forward(order_id)
    run_once()  # attempt 2 fails, retry delay 2**1 = 2 s
    second = seconds_until_available(order_id)

    assert 0.3 < first <= 1.0, first
    assert 1.3 < second <= 2.0, second
    assert second > first


def test_two_workers_never_claim_the_same_job():
    order_ids = [make_job() for _ in range(20)]
    claimed: list[int] = []
    lock = threading.Lock()

    def worker_loop() -> None:
        while True:
            work_id = run_once()
            if work_id is None:
                return
            with lock:
                claimed.append(work_id)

    threads = [threading.Thread(target=worker_loop) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(claimed) == 20
    assert len(set(claimed)) == 20, "a job was claimed by both workers"
    for order_id in order_ids:
        job, order = job_and_order(order_id)
        assert job.status == "completed"
        assert job.attempts == 1, "completed exactly once, never re-claimed"
        assert order.status == "completed"


def test_job_abandoned_by_dead_worker_is_reclaimed_and_completed_once():
    # a worker claimed this job 2 minutes ago and then died
    order_id = make_job(
        order_status="processing", job_status="processing", attempts=1, claimed_seconds_ago=120
    )

    work_id = run_once() or run_once()  # recovery returns it to pending, then it is re-claimed
    assert work_id is not None

    job, order = job_and_order(order_id)
    assert job.status == "completed"
    assert job.attempts == 2
    assert order.status == "completed"
    assert run_once() is None, "no second completion"
