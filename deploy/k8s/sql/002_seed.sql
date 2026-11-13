INSERT INTO products (sku, unit_price_cents)
VALUES
    ('KIT-100', 1299),
    ('PART-200', 599),
    ('PACK-300', 249)
ON CONFLICT (sku) DO UPDATE
SET unit_price_cents = EXCLUDED.unit_price_cents;

INSERT INTO inventory (sku, available)
VALUES
    ('KIT-100', 7),
    ('PART-200', 12),
    ('PACK-300', 5)
ON CONFLICT (sku) DO NOTHING;
