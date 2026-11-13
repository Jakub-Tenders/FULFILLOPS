# FulfillOps - Partner 1(Mohith) Report

## 1. Contribution summary

Partner 1 implemented the application and persistence layer:

- FastAPI HTTP API
- PostgreSQL schema, migrations, and seed data
- SQLAlchemy database models and configuration
- Atomic inventory reservation
- Idempotency handling
- Duplicate SKU aggregation
- Durable worker process
- Bounded retries and dead-letter handling
- Docker Compose local runtime
- API, worker, and contract tests
- Safe disposable test-database reset tooling

## 2. Project objective

FulfillOps is a resilient asynchronous order-processing platform. It accepts
customer orders through an HTTP API, reserves inventory safely, and delegates
order completion to a separate worker.

The system must remain correct when clients retry requests, multiple clients
compete for limited stock, workers restart, or a job fails repeatedly. The
authoritative API behavior is defined in `docs/API_CONTRACT_EN.md`.

## 3. Application architecture

```text
Client
  |
  v
FastAPI API
  |
  | one PostgreSQL transaction
  |-- create order
  |-- reserve inventory
  |-- save idempotency mapping
  `-- create durable work item
  |
  v
PostgreSQL
  |-- products
  |-- inventory
  |-- orders
  |-- order_items
  |-- idempotency_keys
  `-- work_items
        |
        v
   Worker process
        |
        | claim, process, retry
        v
   completed / failed / dead-letter
```

PostgreSQL is the source of truth. Inventory, idempotency state, orders, and
work items are not stored in process-local memory. The architecture decision
is documented in `docs/ADR-001.md`.

## 4. API implementation

The API implements the documented endpoints:

- `GET /health/live`
- `GET /health/ready`
- `GET /api/v1/catalog`
- `GET /api/v1/inventory/{sku}`
- `POST /api/v1/orders`
- `GET /api/v1/orders/{order_id}`
- `GET /metrics`

It also implements the required validation and error behavior, including:

- Missing idempotency key
- Invalid JSON
- Invalid request structure
- Invalid quantity
- Invalid SKU
- Insufficient stock
- Idempotency conflict
- Unknown order
- Request bodies larger than 16 KiB

## 5. Atomic order acceptance

For a new order, the API:

1. Normalizes and hashes the request payload.
2. Locks the idempotency key using a PostgreSQL transaction advisory lock.
3. Checks for an existing key and rejects payload conflicts.
4. Aggregates duplicate SKU lines.
5. Locks inventory rows with `SELECT ... FOR UPDATE`.
6. Verifies that every requested quantity is available.
7. Decreases inventory.
8. Creates the order and order items.
9. Stores the idempotency mapping.
10. Creates a durable work item.
11. Commits all changes together.

The API returns `202 Accepted` only after the order, inventory reservation,
idempotency mapping, and work item have been committed.

This transaction boundary prevents stock from being reserved without a
corresponding durable order.

### Application invariants

- Inventory availability can never become negative.
- One idempotency key maps to one order.
- Replaying an identical request returns the original order ID.
- Reusing a key with a different payload returns
  `409 IDEMPOTENCY_CONFLICT`.
- Duplicate SKU lines are combined before stock and price calculations.
- A committed order survives API and worker restarts.

## 6. Worker reliability

The worker is a separate process and consumes durable records from the
`work_items` table.

- Jobs are claimed inside a database transaction.
- `FOR UPDATE SKIP LOCKED` supports multiple workers.
- A claimed job changes to `processing`.
- The related order changes to `processing`.
- Stale claims are returned to `pending` after the claim timeout.
- Failed jobs retry with bounded exponential backoff.
- Jobs that reach the maximum attempt count become `dead_letter`.
- The corresponding order becomes `failed`.
- Dead-letter records remain available for operator review.

The deterministic `POISON-ORDER` test hook proves bounded failure behavior
without deleting the job or automatically restocking inventory.

## 7. Security and operational safeguards

- Request bodies are limited to 16 KiB.
- Input validation uses strict integer and string types.
- Error responses do not expose SQL, credentials, stack traces, or connection
  strings.
- Database credentials are supplied through environment variables.
- `.env` is ignored by Git; only `.env.example` is committed.
- The Docker image runs as a non-root user.
- The reset script refuses production-like environments and database names.
- Metrics do not contain customer identifiers or idempotency keys.

## 8. Testing and evidence

### Automated tests

The local test suite covers:

- Liveness behavior
- Invalid JSON
- Missing idempotency key
- Invalid quantities
- Unknown orders
- Request-size protection
- Worker claim timeout policy
- Retry backoff policy

Result:

```text
9 passed
```

### Public contract checker

The checker was run against the disposable Docker Compose stack with
concurrency and stress enabled:

```powershell
python fulfillops_public_checker.py `
  --base-url http://localhost:8080 `
  --concurrency `
  --stress
```

Result:

```text
15 passed / 0 failed / 0 skipped
```

The result verifies catalog behavior, validation, idempotent replay, worker
completion, exact inventory reservation, concurrent reservations, same-key
concurrency, duplicate SKU aggregation, and metrics exposure.

### Poison-job evidence

The isolated poison-order demonstration produced:

```text
order status: failed
work item: dead_letter
attempts: 3
last error: Deterministic poison order
```

This demonstrates bounded retries and retained operator-review state.

## 9. Local reproducibility

Start the local stack:

```powershell
docker compose up -d --build
docker compose ps
curl http://localhost:8080/health/ready
```

Run local tests:

```powershell
python -m pytest
```

Reset only the disposable test database:

```powershell
docker compose exec -T `
  -e APP_ENV=test `
  -e DATABASE_URL=postgresql+psycopg://fulfillops:fulfillops@postgres:5432/fulfillops `
  api python scripts/reset_test_data.py
```

## 10. Individual contribution statement

I implemented the FastAPI service, PostgreSQL schema and models, atomic
inventory reservation, idempotency handling, durable worker processing,
bounded retries, dead-letter handling, Docker Compose integration, test reset
tooling, and contract tests.

I validated the implementation with the local test suite and the public
checker, achieving 9 passing local tests and 15 passing public checks with no
failures or skips.

## 11. Assistance disclosure

Complete this section according to the actual submission policy. Record any
AI, framework, documentation, or external code assistance used during
development, including what was accepted, reviewed, and changed by the team.
