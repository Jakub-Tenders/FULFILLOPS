
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from api.main import app


def submit_order(key: str, sku: str, quantity: int = 1):
    """Submit an order using an independent HTTP client."""
    with TestClient(app) as client:
        return client.post(
            "/api/v1/orders",
            headers={"Idempotency-Key": key},
            json={
                "customer_id": "RACE-TEST",
                "items": [
                    {
                        "sku": sku,
                        "quantity": quantity,
                    }
                ],
            },
        )


def print_diagnostics(test_name: str, responses):
    """Print response details to help diagnose concurrency failures."""
    print(f"\n--- {test_name} ---")
    for index, response in enumerate(responses):
        print(
            f"Request {index + 1}: "
            f"HTTP {response.status_code} - {response.text}"
        )


def test_same_idempotency_key_across_eight_requests():
    """Eight concurrent requests must create exactly one order."""
    key = "race-same-key-001"

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(submit_order, key, "KIT-100")
            for _ in range(8)
        ]
        responses = [future.result() for future in futures]

    print_diagnostics(
        "Same idempotency key across eight requests",
        responses,
    )

    assert all(
        response.status_code == 202 for response in responses
    ), "Every request with the same idempotency key should return 202"

    order_ids = {
        response.json()["order_id"]
        for response in responses
    }

    assert len(order_ids) == 1, (
        f"Expected one unique order ID, got {order_ids}"
    )


def test_twelve_different_keys_compete_for_five_units():
    """Only five orders can reserve five available PACK-300 units."""
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [
            executor.submit(
                submit_order,
                f"different-key-{index:03d}",
                "PACK-300",
            )
            for index in range(12)
        ]
        responses = [future.result() for future in futures]

    print_diagnostics(
        "Twelve different keys competing for five units",
        responses,
    )

    accepted = [
        response
        for response in responses
        if response.status_code == 202
    ]

    rejected = [
        response
        for response in responses
        if response.status_code == 409
    ]

    unexpected = [
        response
        for response in responses
        if response.status_code not in (202, 409)
    ]

    assert not unexpected, (
        "Unexpected HTTP responses: "
        + ", ".join(
            f"{response.status_code}: {response.text}"
            for response in unexpected
        )
    )

    assert len(accepted) == 5, (
        f"Expected 5 accepted orders, got {len(accepted)}"
    )

    assert len(rejected) == 7, (
        f"Expected 7 rejected orders, got {len(rejected)}"
    )

    order_ids = {
        response.json()["order_id"]
        for response in accepted
    }

    assert len(order_ids) == 5, (
        f"Expected 5 unique order IDs, got {order_ids}"
    )
