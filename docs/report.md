# FulfillOps: Resilient Asynchronous Order-Processing Platform

## Final project report

**Module:** DevOps Approach  
**Project:** FulfillOps  
**Author:** Project team  
**Report purpose:** Technical overview, implementation evidence, evaluation, and
limitations of the project

---

## 1. Executive summary

FulfillOps is a resilient order-processing platform for a fictional
electronics fulfillment business. The system accepts customer orders through a
FastAPI HTTP service, reserves inventory atomically in PostgreSQL, and
processes accepted orders asynchronously through a separate worker process.
The design addresses the failure modes that make order systems difficult to
operate: client retries, duplicate requests, simultaneous purchases of limited
stock, process restarts, worker crashes, and permanently failing jobs.

The central design decision is to keep authoritative state in PostgreSQL rather
than in API or worker memory. A single transaction creates the order, reserves
the requested stock, records the idempotency mapping, and creates a durable
work item. This means that an HTTP response is returned only after the accepted
order can survive an API restart. The worker later claims the durable work item,
transitions the order, retries failures with bounded exponential backoff, and
retains exhausted jobs in a dead-letter state for operator review.

The API follows the supplied version 1.1 contract. It exposes liveness and
readiness checks, catalog and inventory queries, order creation and lookup, and
Prometheus-format metrics. The implementation also handles duplicate SKU
lines, strict validation, request-size limits, safe error responses, and
idempotent replay. The documented validation and reliability checks include
local tests, concurrent public-checker runs, a poison-order demonstration, and
CI evidence for a failed security scan followed by a corrected green run.

This report describes both the technical solution and the evidence available
in the project materials. Where an operational artifact is not present in the
current project copy, such as Kubernetes manifests or a recorded rolling
update, it is identified as a limitation rather than presented as completed
evidence.

## 2. Problem statement and objectives

An order platform must do more than return a successful HTTP status. It must
protect the consistency of stock and order state when requests arrive
concurrently and when infrastructure fails. FulfillOps was designed around the
following practical scenario:

1. A customer submits an order containing one or more products.
2. The API validates the request and reserves inventory immediately.
3. The customer receives an acceptance receipt without waiting for all
   downstream processing to finish.
4. A separate worker completes the order asynchronously.
5. A retry of the same request must not create another order or reserve stock
   twice.
6. A different order must not obtain stock that has already been committed.
7. A worker crash must not make a committed order disappear.
8. A permanently failing order must become visible to an operator instead of
   retrying forever.

The project objectives were therefore:

- implement the required HTTP contract exactly;
- establish clear data and state invariants;
- make inventory reservation atomic under concurrency;
- provide durable at-least-once work delivery with an exactly-once business
  effect;
- validate the design with unit, integration, concurrency, and black-box
  tests;
- package the services for reproducible local execution;
- apply security and supply-chain safeguards; and
- document decisions, evidence, limitations, and individual contribution.

The authoritative API details are defined in
[`API_CONTRACT_EN.md`](./API_CONTRACT_EN.md), while the architecture decision
is recorded in [`ADR-001.md`](./ADR-001.md).

## 3. Requirements and success criteria

The system has three groups of requirements.

### 3.1 Functional requirements

The catalog contains three seeded products:

| SKU | Unit price | Seed quantity |
|---|---:|---:|
| `KIT-100` | 1,299 cents | 7 |
| `PART-200` | 599 cents | 12 |
| `PACK-300` | 249 cents | 5 |

An order contains a customer identifier and one to ten item lines. Quantities
must be positive integers, SKUs must be known, and a client must provide an
`Idempotency-Key`. The order total is calculated from the persisted catalog
prices. Inventory is decremented when the order is accepted, not when the
worker later completes it.

### 3.2 Reliability requirements

The important invariants are:

- `inventory.available` is never negative;
- one idempotency key maps to at most one order;
- identical replays return the original order identifier;
- a changed payload using an existing key is rejected;
- duplicate SKU lines are aggregated before stock validation;
- an accepted order and its work item are committed together; and
- a worker retry cannot apply the completion effect twice.

### 3.3 Operational requirements

The project brief also calls for reproducible containers, CI quality gates,
security scanning, a software bill of materials, health probes, metrics,
controlled failure demonstrations, and a Kubernetes/GitOps deployment. The
available documentation contains the design and some CI evidence. The report
distinguishes those documented intentions from artifacts verified in the
current project copy.

## 4. Architecture and design decisions

### 4.1 Logical architecture

```text
                         +----------------------+
                         |        Client        |
                         +----------+-----------+
                                    |
                                    | HTTP/JSON
                                    v
                         +----------+-----------+
                         |   FastAPI API        |
                         | validation, metrics  |
                         +----------+-----------+
                                    |
                 one PostgreSQL transaction
             +----------------------+----------------------+
             |                      |                      |
             v                      v                      v
       create order          reserve inventory       save idempotency
             |                                             |
             +----------------------+----------------------+
                                    |
                                    v
                         durable work_items record
                                    |
                                    v
                         +----------+-----------+
                         |  PostgreSQL          |
                         | source of truth      |
                         +----------+-----------+
                                    ^
                                    |
                         +----------+-----------+
                         |  Worker process     |
                         | claim, complete,    |
                         | retry, dead-letter  |
                         +----------------------+
```

The API and worker are separate processes. PostgreSQL stores products,
inventory, orders, order items, idempotency mappings, and durable work items.
This keeps business state independent of the lifetime of any individual
container. The database is not merely a cache or a queue backup; it is the
authoritative source for both order state and inventory.

### 4.2 Why PostgreSQL was selected

PostgreSQL provides the transaction and locking primitives required by the
problem without introducing a second mandatory stateful service. The same
database can atomically persist the order and its work item, lock inventory
rows, enforce uniqueness, and recover work after a worker process terminates.
It is also straightforward to reproduce locally with Docker Compose and can
support multiple API and worker replicas.

The trade-off is that the worker state machine must be implemented carefully.
At-least-once delivery means a job may be observed more than once after a
lease expires, so the order transition and completion effect must be guarded
by persisted state. The design accepts this complexity in exchange for a
small, durable architecture that is easy to inspect and test.

### 4.3 Data model

The principal entities are:

- **products:** SKU and unit price;
- **inventory:** current available quantity per SKU;
- **orders:** customer, status, total, and timestamps;
- **order_items:** the normalized product quantities belonging to an order;
- **idempotency_keys:** normalized request hash and the associated order ID;
  and
- **work_items:** durable processing state, attempts, lease information,
  backoff time, and the last error.

The schema migration and seed data provide a repeatable starting point. A
unique idempotency constraint prevents two committed mappings for the same
client key. Foreign keys preserve order/item and item/product relationships.
The work table retains failed records rather than silently deleting them.

## 5. API implementation

The service implements the required routes:

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | process liveness, independent of the database |
| `GET /health/ready` | database and migration readiness |
| `GET /api/v1/catalog` | seeded products and prices |
| `GET /api/v1/inventory/{sku}` | current non-negative availability |
| `POST /api/v1/orders` | validate, reserve, persist, and queue an order |
| `GET /api/v1/orders/{order_id}` | retrieve order status and total |
| `GET /metrics` | Prometheus-format operational metrics |

The service uses machine-readable error codes. Examples include
`INVALID_JSON`, `INVALID_REQUEST`, `INVALID_QUANTITY`, `INVALID_SKU`,
`OUT_OF_STOCK`, `IDEMPOTENCY_REQUIRED`, `IDEMPOTENCY_CONFLICT`, and
`ORDER_NOT_FOUND`. Error messages are non-empty and safe: they do not reveal
SQL statements, credentials, stack traces, or connection strings.

Input handling is intentionally strict. The API rejects missing or malformed
idempotency keys, empty customer identifiers, invalid item structures,
non-positive quantities, unknown SKUs, and requests larger than 16 KiB.
Boolean values are not accepted as integer quantities. These checks reduce
ambiguous behavior and prevent an invalid request from reaching the
reservation transaction.

### 5.1 Order acceptance flow

For a new order, the application performs the following sequence:

1. Parse and validate the request.
2. Normalize the payload and calculate a stable request hash.
3. Acquire a PostgreSQL transaction advisory lock for the idempotency key.
4. Look up an existing mapping.
5. Return the original order for an identical replay, or reject a changed
   payload with `409 IDEMPOTENCY_CONFLICT`.
6. Aggregate repeated SKU lines.
7. Lock the relevant inventory rows with `SELECT ... FOR UPDATE`.
8. Check all requested quantities before making any change.
9. Decrement inventory.
10. Create the order and normalized order items.
11. Insert the idempotency mapping.
12. Insert a durable work item.
13. Commit the transaction.

The `202 Accepted` response is sent only after step 13. Consequently, a
successful response means that the order, reservation, replay mapping, and
future work are all durable. If any step fails, the transaction rolls back and
there is no partial reservation.

### 5.2 Duplicate SKU aggregation

A request can contain the same SKU on multiple lines. The service combines
those lines before checking availability and calculating the order total. For
example, two lines for `KIT-100` with quantities four and four become one
effective request for eight units. Since the seed inventory is seven, the
whole order is rejected as `OUT_OF_STOCK`; four units are not reserved
partially. This behavior is important because validating each line
independently would make the final result dependent on line ordering.

## 6. Worker processing and failure handling

The worker continuously searches for pending or recoverable work. It claims a
job inside a database transaction and records a lease. PostgreSQL
`FOR UPDATE SKIP LOCKED` allows multiple workers to make progress without
waiting on jobs already claimed by another worker.

The work-item state machine is conceptually:

```text
pending -> processing -> completed
    ^          |
    |          v
    +------ retryable failure
                   |
                   v
             dead_letter
```

When a job is claimed, the associated order moves to `processing`. If the
worker completes successfully, the order moves to `completed` and the work
item is finalized. If the worker fails, the attempt count increases and the
next execution time is calculated using bounded exponential backoff. Once the
maximum attempt count is reached, the work item becomes `dead_letter` and the
order becomes `failed`.

The claim timeout is essential for crash recovery. If a worker terminates
after claiming a job but before completing it, the lease eventually becomes
stale. Another worker can then return the item to `pending` and process it.
Because the business transition is guarded by persisted order state, recovery
does not complete the same order twice.

The deterministic `POISON-ORDER` hook is used only for testing. It produces a
repeatable failure, demonstrates bounded retries, and leaves the failed work
available for operator review. The design deliberately does not automatically
restock inventory after a permanent failure: that decision requires business
reconciliation and should not be guessed by infrastructure code.

## 7. Testing strategy and results

The testing approach is layered so that a passing black-box test is supported
by smaller, diagnosable checks.

### 7.1 Test layers

| Layer | Main purpose |
|---|---|
| Unit | validation, normalization, aggregation, totals, retry policy |
| Integration | order acceptance, persistence, worker completion, replay |
| Race | same-key concurrency and competing-key stock contention |
| Worker | claim recovery, backoff, poison jobs, no double completion |
| Black-box | supplied public HTTP contract checker |
| Operational | rollout, fault injection, metrics, and recovery evidence |

All destructive tests are intended for an isolated test database. The reset
tooling checks the application environment and database naming before
recreating test data. This prevents a test convenience from becoming a
production data-loss mechanism.

### 7.2 Local checks

The Partner 2 integration branch records seventeen passing pytest tests. These
cover liveness, invalid JSON, required idempotency keys, invalid quantities,
unknown orders, request-size protection, same-key and competing-key races,
stale-claim behavior, retry backoff, poison jobs, two-worker claim safety, and
worker crash recovery. This is stronger evidence than the earlier nine-test
baseline because it includes the independently owned race and worker
reliability tests.

The test plan also defines the two most important race scenarios:

1. Eight simultaneous requests with the same idempotency key across two API
   instances must return one stable order ID and decrement stock once.
2. Twelve distinct-key requests competing for five `PACK-300` units must
   accept exactly five and reject seven with `OUT_OF_STOCK`, without negative
   inventory.

Other important cases include changed payloads under an existing key,
reordered JSON keys, replay after an API restart, duplicate SKU aggregation,
worker crash recovery, poison-job termination, and increasing retry gaps.

The recorded Partner 2 branch results are:

```text
2 cross-replica race tests passed
17 local pytest tests passed
```

The cross-replica tests run eight identical-key requests and twelve
different-key requests against two API services backed by the same PostgreSQL
database. The worker reliability suite verifies that two workers do not claim
the same job, that a job abandoned by a killed worker is reclaimed and
completed once, and that retry delay remains bounded.

### 7.3 Public contract checker

The public checker was run against a disposable Docker Compose stack with
concurrency and stress enabled:

```powershell
python fulfillops_public_checker.py `
  --base-url http://localhost:8080 `
  --concurrency `
  --stress
```

Recorded result:

```text
15 passed / 0 failed / 0 skipped
```

The result covers catalog behavior, request validation, idempotent replay,
worker completion, exact inventory reservation, concurrent reservations,
same-key concurrency, duplicate SKU aggregation, and metrics exposure. Since
the checker consumes inventory, the run must always use a freshly seeded
isolated test database.

### 7.4 Poison-job result

The isolated poison-order demonstration produced:

```text
order status: failed
work item: dead_letter
attempts: 3
last error: Deterministic poison order
```

This is evidence that the worker stops after a bounded number of attempts and
retains the failure instead of deleting it or entering an infinite retry loop.

## 8. DevOps, containerization, and delivery

### 8.1 Local reproducibility

The intended local workflow is:

```powershell
docker compose up -d --build
docker compose ps
curl http://localhost:8080/health/ready
python -m pytest
```

The API, worker, and PostgreSQL services are separated so that the worker can
be restarted independently of the HTTP process. PostgreSQL uses persistent
storage. The runtime should be rebuilt from explicit dependency manifests,
with environment variables supplying credentials rather than committing
secrets.

The container hardening requirements include a non-root runtime user, minimal
runtime dependencies, a `.dockerignore`, explicit versions, and health checks.
The safe reset command is restricted to the disposable test environment.

### 8.2 CI quality gates

The documented CI pipeline contains linting, tests, contract checks, Docker
image build, vulnerability scanning, and SBOM generation. The pipeline
evidence demonstrates a useful red-to-green quality gate:

- the initial run failed because Trivy found five fixable HIGH findings;
- the affected OS and Python dependencies were upgraded;
- the Docker image OS packages were patched; and
- the corrected run passed lint/tests/checker and image/scan/SBOM jobs.

The documented green run reports the lint/tests/checker and image/scan/SBOM
jobs as successful. The branch-local evidence records seventeen pytest tests
and a successful concurrency/stress checker run. The SBOM is generated in SPDX JSON format
using Syft tooling and is retained as a CI artifact. This makes the dependency
inventory inspectable rather than relying only on a successful build.

The project also records a supply-chain lesson: action references must be
reviewed and pinned to safe, existing releases. A non-existent or compromised
action tag can undermine an otherwise correct pipeline.

### 8.3 Kubernetes and GitOps status

The assignment expects two API replicas, two worker instances, persistent
storage, readiness/liveness probes, resource limits, Argo CD reconciliation,
drift correction, and safe rolling-release recovery. These requirements are
important because a local Compose test alone cannot prove cross-replica
behavior or deployment recovery.

The current project copy contains the architecture, setup guidance, rollout
probe, and CI evidence, but it does not contain Kubernetes manifests, an Argo
CD Application, or a recorded 30-second rollout CSV. The Partner 2 branch
does, however, provide the local multi-instance equivalent: `api` and `api2`
run as separate API services, while the worker is configured with two
replicas. Kubernetes and GitOps should still be treated as remaining evidence
to collect before final submission, not as verified completed deliverables in
this report.

## 9. Security and operational safeguards

Security is implemented as a set of small controls across the request path,
runtime, database, and delivery pipeline:

- request bodies are capped at 16 KiB;
- validation rejects malformed and out-of-range values early;
- errors never expose SQL, credentials, stack traces, or connection strings;
- credentials are supplied through environment variables;
- `.env` is ignored and only placeholders belong in `.env.example`;
- the container runs as a non-root user;
- the reset script refuses production-like environments and database names;
- metrics do not contain customer identifiers or idempotency keys; and
- dependency scanning and SBOM generation are part of CI.

The main threats addressed are:

1. **Overselling under concurrency:** prevented by row locks and transactional
   stock updates.
2. **Duplicate order creation:** prevented by idempotency locking and a unique
   database mapping.
3. **Payload substitution under a reused key:** prevented by normalized
   payload hashing and conflict detection.
4. **Request abuse through oversized or malformed input:** reduced by size
   limits and strict validation.
5. **Credential leakage:** reduced by environment-based configuration,
   safe errors, and secret exclusions from Git.
6. **Infinite retry loops:** prevented by maximum attempts and dead-letter
   state.
7. **Supply-chain vulnerabilities:** detected by the blocking Trivy gate and
   corrected through dependency and image updates.

These controls provide defense in depth, but they do not replace production
identity management, network policy, encrypted secret storage, database
backup testing, or centralized audit logging.

## 10. Observability and incident response

The API exposes Prometheus-format metrics for:

- `fulfillops_orders_created_total`;
- `fulfillops_orders_failed_total`;
- `fulfillops_worker_retries_total`;
- `fulfillops_http_requests_total`; and
- a latency histogram or equivalent measurement for p95 calculation.

Liveness intentionally remains independent of PostgreSQL: it answers whether
the process is alive. Readiness checks whether the database and required
migrations are available for safe order acceptance. This distinction helps a
deployment controller restart or remove an unhealthy instance without
mistaking a database outage for a dead process.

The recommended incident workflow is:

1. identify whether the problem is API availability, database readiness, or
   worker progress;
2. inspect health responses, structured logs, and retry metrics;
3. verify that committed orders and inventory records still exist;
4. restore capacity or allow a replacement worker to reclaim stale work; and
5. document timeline, impact, root cause, recovery, and prevention actions.

The project brief requires two controlled failure demonstrations: killing a
worker during processing and stopping an unhealthy API rollout. The worker
design supports the first through leases and reclaimable work. The available
project copy does not include the final recorded incident timeline or rollout
probe output, so those should be added as evidence before submission.

## 11. Team contributions

### 11.1 Partner 1: application and persistence

Partner 1 implemented the application and persistence foundation:

- FastAPI service and HTTP route behavior;
- PostgreSQL schema, migration, seed data, and configuration;
- SQLAlchemy database models;
- atomic inventory reservation;
- idempotency handling and duplicate SKU aggregation;
- durable worker processing;
- bounded retries and dead-letter handling;
- Docker Compose local runtime integration;
- API, worker, and contract-test support; and
- safe disposable test-database reset tooling.

The contribution was validated through the public checker, the poison-job
demonstration, and the documented CI evidence. The main technical ownership
was the transaction boundary that connects an accepted order to inventory,
idempotency state, and durable work.

### 11.2 Partner 2: testing, infrastructure, and delivery

Partner 2 implemented and documented the project verification and runtime
infrastructure:

- unit, contract, race, worker-state, and worker-reliability tests;
- cross-replica HTTP race tests using `api` and `api2`;
- two-worker claim-safety and abandoned-job recovery tests;
- the worker crash drill and its recorded recovery evidence;
- the deterministic poison-job drill and dead-letter evidence;
- safe test-database reset tooling and test-environment guards;
- Dockerfile and Docker Compose services for PostgreSQL, two API instances,
  and two workers;
- non-root container execution and health checks;
- the GitHub Actions workflow for linting, tests, contract checks, image
  building, vulnerability scanning, and SBOM publication; and
- the CI evidence documenting the red Trivy run and corrected green run.

The recorded worker crash drill killed one worker while four jobs were in
flight. The surviving worker eventually completed all four orders, and the
abandoned job was reclaimed with its attempt count increased to two. The
poison-job drill reached `dead_letter` after three attempts, changed the order
to `failed`, and kept the reserved stock unreplenished for manual
reconciliation.

### 11.3 Joint ownership

The complete solution depends on both contributions. Partner 1's transaction
and worker implementation provides the behavior being tested; Partner 2's
tests, Compose topology, CI workflow, and evidence demonstrate that behavior
under concurrency and failure. Both partners should understand the system end
to end because the individual defense can examine any failure path.

## 12. Limitations and future improvements

The implementation has several limitations that are appropriate to state
explicitly:

1. **Some operational evidence is incomplete in this project copy.**
   Kubernetes manifests, Argo CD configuration, rollout CSV evidence, and a
   completed incident postmortem are not present here. The local Compose
   equivalent and worker failure evidence are present on the Partner 2 branch.
2. **Local performance results are environment-specific.** A p95 measured on a
   laptop cannot represent production latency or capacity. A future report
   should include at least 200 timestamped samples and state the hardware,
   database, concurrency, and network conditions.
3. **Dead-letter jobs require human reconciliation.** Retaining stock is safe
   against accidental double fulfillment, but an operational procedure is
   needed to cancel, retry, or manually reconcile a failed order.
4. **The metrics surface is intentionally small.** Production operations would
   benefit from database pool saturation, queue age, lease-expiry counts,
   worker throughput, and deployment metadata.
5. **PostgreSQL is a single durable dependency.** High availability, backups,
   restore drills, and failover would be necessary for a production service.
6. **The poison hook is a test-only mechanism.** It must remain disabled or
   inaccessible in a production environment.

Future work should first close the evidence gaps: add the Kubernetes and Argo
CD files, execute the two-worker crash drill, capture the red-to-green rollout
recovery, calculate p95 from 200 or more samples, and attach the resulting
logs and CSVs. The next product-level improvement would be an authenticated
operator workflow for dead-letter review and reconciliation.

## 13. Conclusion

FulfillOps demonstrates the core engineering principle of a reliable order
system: correctness must be established at the persistence boundary, not
assumed from a successful HTTP response. PostgreSQL transactions and row-level
locking protect inventory; durable idempotency mappings protect retries; and
the work table allows an independent worker to recover after failure. Bounded
retry and dead-letter states make failure visible and finite.

The documented evidence shows successful contract behavior, concurrent
reservation handling, worker completion, poison-job termination, and a CI
security gate that detects and then resolves vulnerable dependencies. The
remaining work is primarily operational proof: Kubernetes/GitOps artifacts,
recorded fault drills, and complete performance and incident evidence. Clearly
separating verified behavior from planned evidence keeps the report credible
and provides a precise checklist for final project completion.

## 14. Reproducibility appendix

### Start the local stack

```powershell
docker compose up -d --build
docker compose ps
curl http://localhost:8080/health/ready
```

### Run tests

```powershell
python -m pytest
```

### Run the contract checker

Run only against a fresh isolated test database:

```powershell
python fulfillops_public_checker.py `
  --base-url http://localhost:8080 `
  --concurrency `
  --stress
```

### Reset the disposable test database

```powershell
docker compose exec -T `
  -e APP_ENV=test `
  -e DATABASE_URL=postgresql+psycopg://fulfillops:fulfillops@postgres:5432/fulfillops `
  api python scripts/reset_test_data.py
```

### Assistance disclosure

Complete this section according to the actual submission policy. Record any
AI, framework, documentation, or external code assistance used during
development, including what was accepted, reviewed, and changed by the team.
