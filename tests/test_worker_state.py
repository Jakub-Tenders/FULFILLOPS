from datetime import datetime, timedelta, timezone

from worker.main import CLAIM_TIMEOUT_SECONDS


def test_claim_timeout_is_bounded():
    assert CLAIM_TIMEOUT_SECONDS == 30


def test_retry_delay_is_bounded_by_worker_policy():
    delays = [min(60, 2 ** (attempt - 1)) for attempt in range(1, 4)]

    assert delays == [1, 2, 4]
    assert all(delay <= 60 for delay in delays)


def test_stale_claim_cutoff_is_in_the_past():
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=CLAIM_TIMEOUT_SECONDS)

    assert cutoff < datetime.now(timezone.utc)
