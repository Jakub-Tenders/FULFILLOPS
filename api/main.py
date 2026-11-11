import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .db import get_db
from .metrics import http_latency, http_requests, orders_created
from .models import IdempotencyKey, Inventory, Order, OrderItem, Product, WorkItem
from .schemas import CreateOrderRequest, CreateOrderResponse, OrderResponse

logger = logging.getLogger("fulfillops.api")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    max_body_size = 16 * 1024

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                body_size = int(content_length)
            except ValueError:
                return error_response(400, "INVALID_REQUEST", "Invalid Content-Length")
            if body_size > self.max_body_size:
                return error_response(413, "REQUEST_TOO_LARGE", "Request body exceeds 16 KiB")
        return await call_next(request)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="FulfillOps API", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestSizeLimitMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    if any(error.get("type") == "json_invalid" for error in errors):
        return error_response(400, "INVALID_JSON", "Request body is not valid JSON")
    if any("quantity" in error.get("loc", ()) for error in errors):
        return error_response(422, "INVALID_QUANTITY", "Quantity must be an integer from 1 to 20")
    return error_response(400, "INVALID_REQUEST", "Request has an invalid structure")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException):
    return error_response(exc.status_code, "INVALID_REQUEST", "Request is invalid")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - started
    path = request.url.path
    http_requests.labels(request.method, path, str(response.status_code)).inc()
    http_latency.labels(request.method, path).observe(duration)
    return response


def error_response(
    status_code: int, code: str, message: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        required_tables = (
            "products",
            "inventory",
            "orders",
            "order_items",
            "idempotency_keys",
            "work_items",
        )
        result = db.execute(
            text(
                "SELECT COUNT(*) FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public' AND tablename IN "
                "('products', 'inventory', 'orders', 'order_items', "
                "'idempotency_keys', 'work_items')"
            )
        )
        if result.scalar_one() != len(required_tables):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not_ready"},
            )
    except SQLAlchemyError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready"},
        )
    return {"status": "ready"}


@app.get("/api/v1/catalog")
def catalog(db: Session = Depends(get_db)) -> dict[str, list[dict[str, int | str]]]:
    products = db.scalars(select(Product).order_by(Product.sku)).all()
    return {
        "products": [
            {"sku": product.sku, "unit_price_cents": product.unit_price_cents}
            for product in products
        ]
    }


@app.get("/api/v1/inventory/{sku}")
def inventory(sku: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    item = db.get(Inventory, sku)
    if item is None:
        return error_response(404, "SKU_NOT_FOUND", "SKU not found")
    return {"sku": item.sku, "available": item.available}


@app.post(
    "/api/v1/orders",
    response_model=CreateOrderResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_order(
    request: CreateOrderRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> CreateOrderResponse | JSONResponse:
    if idempotency_key is None:
        return error_response(400, "IDEMPOTENCY_REQUIRED", "Idempotency-Key is required")
    if not 8 <= len(idempotency_key) <= 128 or not idempotency_key.isascii() or not all(
        32 <= ord(character) <= 126 for character in idempotency_key
    ):
        return error_response(400, "INVALID_REQUEST", "Invalid Idempotency-Key")

    normalized_payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    payload_hash = hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
    quantities: dict[str, int] = {}
    for item in request.items:
        quantities[item.sku] = quantities.get(item.sku, 0) + item.quantity

    try:
        with db.begin():
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": idempotency_key},
            )
            existing = db.get(IdempotencyKey, idempotency_key)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    return error_response(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "Idempotency-Key was used with a different payload",
                    )
                existing_order = db.get(Order, existing.order_id)
                if existing_order is None:
                    return error_response(500, "ORDER_STATE_ERROR", "Order record is unavailable")
                return CreateOrderResponse(
                    order_id=existing_order.order_id, status="queued"
                )

            products = {
                product.sku: product
                for product in db.scalars(
                    select(Product).where(Product.sku.in_(quantities.keys()))
                ).all()
            }
            missing_skus = set(quantities) - set(products)
            if missing_skus:
                return error_response(422, "INVALID_SKU", "One or more SKUs are invalid")

            inventory_rows = {
                item.sku: item
                for item in db.scalars(
                    select(Inventory)
                    .where(Inventory.sku.in_(quantities.keys()))
                    .with_for_update()
                ).all()
            }
            if any(
                inventory_rows[sku].available < quantity
                for sku, quantity in quantities.items()
            ):
                return error_response(409, "OUT_OF_STOCK", "Not enough items available")

            order_id = uuid4()
            total_cents = 0
            order_items: list[OrderItem] = []
            for sku, quantity in quantities.items():
                inventory_rows[sku].available -= quantity
                price = products[sku].unit_price_cents
                total_cents += price * quantity
                order_items.append(
                    OrderItem(
                        order_id=order_id,
                        sku=sku,
                        quantity=quantity,
                        unit_price_cents=price,
                    )
                )

            db.add(
                Order(
                    order_id=order_id,
                    customer_id=request.customer_id,
                    status="queued",
                    total_cents=total_cents,
                    items=order_items,
                )
            )
            db.flush()
            db.add(
                IdempotencyKey(
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    order_id=order_id,
                )
            )
            db.add(WorkItem(order_id=order_id, status="pending"))
        orders_created.inc()
        return CreateOrderResponse(order_id=order_id, status="queued")
    except SQLAlchemyError:
        db.rollback()
        logger.exception("order_creation_failed")
        return error_response(503, "SERVICE_UNAVAILABLE", "Order service is unavailable")


@app.get("/api/v1/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)) -> OrderResponse | JSONResponse:
    try:
        parsed_order_id = UUID(order_id)
    except ValueError:
        return error_response(404, "ORDER_NOT_FOUND", "Order not found")
    order = db.get(Order, parsed_order_id)
    if order is None:
        return error_response(404, "ORDER_NOT_FOUND", "Order not found")
    return OrderResponse(
        order_id=order.order_id, status=order.status, total_cents=order.total_cents
    )


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
