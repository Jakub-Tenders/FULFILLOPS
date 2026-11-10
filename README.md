# FulfillOps

Resilient asynchronous order-processing platform for the DevOps capstone.

## Development status

The application baseline is being implemented. See the supplied
`API_CONTRACT_EN.md` for the authoritative HTTP contract and
`docs/ADR-001.md` for the initial architecture decision.

## Planned services

- `api`: FastAPI HTTP service
- `worker`: durable asynchronous order processor
- `postgres`: persistent database
