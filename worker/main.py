
import logging
import time
from datetime import datetime, timedelta, timezone

from prometheus_client import start_http_server
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import SessionLocal
from api.metrics import (
    worker_completed,
    worker_dead_letter,
    worker_retries,
)
from api.models import Order, WorkItem

logger = logging.getLogger("fulfillops.worker")
settings = get_settings()

CLAIM_TIMEOUT_SECONDS = 30


def recover_stale_claims(db: Session) -> int:
    """Recover abandoned jobs without exceeding the retry limit."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=CLAIM_TIMEOUT_SECONDS
    )
    now = datetime.now(timezone.utc)

    # Jobs that have exhausted their attempts must be dead-lettered.
    exhausted_order_ids = db.scalars(
        update(WorkItem)
        .where(
            WorkItem.status == "processing",
            WorkItem.claimed_at < cutoff,
            WorkItem.attempts >= settings.worker_max_attempts,
        )
        .values(
            status="dead_letter",
            claimed_at=None,
            last_error="Worker claim expired after maximum attempts",
        )
        .returning(WorkItem.order_id)
    ).all()

    # Their associated orders must also be marked failed.
    if exhausted_order_ids:
        db.execute(
            update(Order)
            .where(Order.order_id.in_(exhausted_order_ids))
            .values(status="failed")
        )

    # Only jobs with attempts remaining can return to pending.
    retried_order_ids = db.scalars(
        update(WorkItem)
        .where(
            WorkItem.status == "processing",
            WorkItem.claimed_at < cutoff,
            WorkItem.attempts < settings.worker_max_attempts,
        )
        .values(
            status="pending",
            claimed_at=None,
            available_at=now,
            last_error="Worker claim expired; retrying",
        )
        .returning(WorkItem.order_id)
    ).all()

    if exhausted_order_ids:
        worker_dead_letter.inc(len(exhausted_order_ids))

    if retried_order_ids:
        worker_retries.inc(len(retried_order_ids))

    recovered = len(exhausted_order_ids) + len(retried_order_ids)

    if recovered:
        logger.warning(
            "stale_claims_recovered",
            extra={
                "dead_lettered": len(exhausted_order_ids),
                "requeued": len(retried_order_ids),
            },
        )

    return recovered


def claim_work(db: Session) -> int | None:
    with db.begin():
        recover_stale_claims(db)

        work_item = db.scalars(
            select(WorkItem)
            .where(
                WorkItem.status == "pending",
                WorkItem.available_at <= text("CURRENT_TIMESTAMP"),
            )
            .order_by(WorkItem.work_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()

        if work_item is None:
            return None

        work_item.status = "processing"
        work_item.claimed_at = datetime.now(timezone.utc)
        work_item.attempts += 1

        db.execute(
            update(Order)
            .where(
                Order.order_id == work_item.order_id,
                Order.status == "queued",
            )
            .values(status="processing")
        )

        return work_item.work_id


def process_work(work_id: int) -> None:
    with SessionLocal() as db:
        work_item = db.get(WorkItem, work_id)

        if work_item is None or work_item.status != "processing":
            return

        order = db.get(Order, work_item.order_id)

        if order is None:
            db.rollback()
            _record_failure(
                db, work_item, "Order record is missing"
            )
            return

        try:
            _process_order(order)

        except ValueError as exc:
            db.rollback()
            _record_failure(db, work_item, str(exc))
            return

        db.rollback()

        with db.begin():
            current_work = db.get(WorkItem, work_id)
            current_order = db.get(Order, order.order_id)

            if (
                current_work is None
                or current_order is None
                or current_work.status != "processing"
            ):
                return

            current_order.status = "completed"
            current_work.status = "completed"
            current_work.claimed_at = None
            current_work.last_error = None

        worker_completed.inc()


def _process_order(order: Order) -> None:
    # Deterministic failure hook for retry/dead-letter testing.
    if order.customer_id == "POISON-ORDER":
        raise ValueError("Deterministic poison order")


def _record_failure(
    db: Session,
    work_item: WorkItem,
    message: str,
) -> None:
    with db.begin():
        current_work = db.get(WorkItem, work_item.work_id)
        current_order = db.get(Order, work_item.order_id)

        if current_work is None or current_order is None:
            return

        current_work.last_error = message
        current_work.claimed_at = None

        if current_work.attempts >= settings.worker_max_attempts:
            current_work.status = "dead_letter"
            current_order.status = "failed"
            worker_dead_letter.inc()

        else:
            delay = min(60, 2 ** (current_work.attempts - 1))

            current_work.status = "pending"
            current_work.available_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=delay)
            )

            worker_retries.inc()


def run_worker() -> None:
    logger.info("worker_started")

    # Expose Prometheus metrics on port 9100.
    start_http_server(9100)

    while True:
        try:
            with SessionLocal() as db:
                work_id = claim_work(db)

            if work_id is None:
                time.sleep(settings.worker_poll_interval_seconds)
                continue

            # Allows deliberate termination during processing.
            if settings.worker_process_delay_seconds > 0:
                time.sleep(settings.worker_process_delay_seconds)

            process_work(work_id)

        except SQLAlchemyError:
            logger.exception("worker_database_error")
            time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    run_worker()
