from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class OrderItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: StrictStr
    quantity: StrictInt = Field(ge=1, le=20)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: StrictStr = Field(min_length=1, max_length=64)
    items: list[OrderItemRequest] = Field(min_length=1, max_length=10)


class CreateOrderResponse(BaseModel):
    order_id: UUID
    status: str


class OrderResponse(BaseModel):
    order_id: UUID
    status: str
    total_cents: int
