import json

from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_liveness_is_independent_of_database():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_invalid_json_uses_contract_error():
    response = client.post(
        "/api/v1/orders",
        content=b"{not-json",
        headers={"Content-Type": "application/json", "Idempotency-Key": "contract-001"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_JSON"


def test_missing_idempotency_key_is_rejected():
    response = client.post(
        "/api/v1/orders",
        json={"customer_id": "CUST-001", "items": [{"sku": "KIT-100", "quantity": 1}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_REQUIRED"


def test_invalid_quantity_is_rejected():
    response = client.post(
        "/api/v1/orders",
        json={"customer_id": "CUST-001", "items": [{"sku": "KIT-100", "quantity": 0}]},
        headers={"Idempotency-Key": "contract-002"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUANTITY"


def test_unknown_order_id_is_contract_not_found():
    response = client.get("/api/v1/orders/not-real-order")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_request_body_limit_is_enforced():
    payload = json.dumps({"customer_id": "x" * 17000}).encode()
    response = client.post(
        "/api/v1/orders",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Idempotency-Key": "contract-003",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
