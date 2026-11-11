"""
Race tests over real HTTP against both API replicas (8080 and 8081).
Needs the Compose stack running: `docker compose up -d --build --wait --scale worker=2`.
Seed stock: KIT-100=7, PART-200=12, PACK-300=5 (reset before every test by conftest).
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

REPLICAS = ["http://localhost:8080", "http://localhost:8081"]


def _replicas_ready() -> bool:
    try:
        return all(httpx.get(f"{url}/health/ready", timeout=2).status_code == 200 for url in REPLICAS)
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _replicas_ready(), reason="both API replicas (8080, 8081) must be running"
)


def post_order(base_url: str, key: str, sku: str, quantity: int = 1) -> httpx.Response:
    body = {"customer_id": "REPLICA-TEST", "items": [{"sku": sku, "quantity": quantity}]}
    with httpx.Client(base_url=base_url, timeout=15) as client:
        return client.post("/api/v1/orders", json=body, headers={"Idempotency-Key": key})


def available(sku: str) -> int:
    with httpx.Client(base_url=REPLICAS[0], timeout=10) as client:
        return client.get(f"/api/v1/inventory/{sku}").json()["available"]


def fire_together(count: int, make_request):
    """Release `count` requests at the same instant, alternating replicas."""
    barrier = threading.Barrier(count)

    def run(index: int):
        barrier.wait()
        return make_request(REPLICAS[index % 2], index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def test_same_key_eight_requests_across_both_replicas():
    key = "replica-same-key-001"

    responses = fire_together(8, lambda url, _: post_order(url, key, "KIT-100"))

    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    assert len({r.json()["order_id"] for r in responses}) == 1, "one key must give one order"
    assert available("KIT-100") == 6, "stock must be reserved exactly once (7 -> 6)"


def test_twelve_different_keys_across_both_replicas_for_five_units():
    responses = fire_together(
        12, lambda url, i: post_order(url, f"replica-different-{i:03d}", "PACK-300")
    )

    accepted = [r for r in responses if r.status_code == 202]
    rejected = [r for r in responses if r.status_code == 409]
    assert len(accepted) == 5, [r.status_code for r in responses]
    assert len(rejected) == 7
    assert all(r.json()["error"]["code"] == "OUT_OF_STOCK" for r in rejected)
    assert len({r.json()["order_id"] for r in accepted}) == 5
    assert available("PACK-300") == 0, "stock must hit zero and never go negative"
