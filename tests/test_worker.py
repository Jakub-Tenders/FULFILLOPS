
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from api.config import get_settings
from api.db import SessionLocal
from api.models import Order, WorkItem
from worker.main import recover_stale_claims


def test_stale_job_at_max_attempts_goes_to_dead_letter():
    order_id = uuid4()
    settings = get_settings()

    with SessionLocal.begin() as db:
        db.add(
            Order(
                order_id=order_id,
                customer_id="STALE-TEST",
                status="processing",
                total_cents=1299,
            )
        )

        db.flush()

        db.add(
            WorkItem(
                order_id=order_id,
                status="processing",
                attempts=settings.worker_max_attempts,
                claimed_at=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=120)
                ),
            )
        )

    with SessionLocal() as db:
        with db.begin():
            recovered = recover_stale_claims(db)

        assert recovered == 1

        work_item = db.scalar(
            select(WorkItem).where(WorkItem.order_id == order_id)
        )
        order = db.get(Order, order_id)

        assert work_item.status == "dead_letter"
        assert work_item.claimed_at is None
        assert order.status == "failed"


def test_stale_job_with_attempts_remaining_returns_to_pending():
    order_id = uuid4()
    settings = get_settings()

    with SessionLocal.begin() as db:
        db.add(
            Order(
                order_id=order_id,
                customer_id="RETRY-TEST",
                status="processing",
                total_cents=1299,
            )
        )

        db.flush()

        db.add(
            WorkItem(
                order_id=order_id,
                status="processing",
                attempts=max(1, settings.worker_max_attempts - 1),
                claimed_at=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=120)
                ),
            )
        )

    with SessionLocal() as db:
        with db.begin():
            recovered = recover_stale_claims(db)

        assert recovered == 1

        work_item = db.scalar(
            select(WorkItem).where(WorkItem.order_id == order_id)
        )

        assert work_item.status == "pending"
        assert work_item.claimed_at is None
