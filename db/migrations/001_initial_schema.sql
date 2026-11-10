CREATE TABLE products (
    sku VARCHAR(32) PRIMARY KEY,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);

CREATE TABLE inventory (
    sku VARCHAR(32) PRIMARY KEY REFERENCES products (sku),
    available INTEGER NOT NULL CHECK (available >= 0)
);

CREATE TABLE orders (
    order_id UUID PRIMARY KEY,
    customer_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    order_id UUID NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    sku VARCHAR(32) NOT NULL REFERENCES products (sku),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE idempotency_keys (
    idempotency_key VARCHAR(128) PRIMARY KEY,
    payload_hash CHAR(64) NOT NULL,
    order_id UUID NOT NULL UNIQUE REFERENCES orders (order_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE work_items (
    work_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders (order_id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX work_items_claim_idx
    ON work_items (available_at, work_id)
    WHERE status IN ('pending', 'processing');
