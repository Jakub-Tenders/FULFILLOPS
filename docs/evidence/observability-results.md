# FulfillOps — Observability & Availability Evidence

## Part A — Rolling Update Availability Test

* **Branch:** `main`
* **Target Endpoint:** `http://localhost:8080/health/ready`
* **Environment:** Local Kubernetes cluster (`kind-fulfillops`, namespace `fulfillops`)
* **Deployment:** `deployment/fulfillops-api` (2 replicas)
* **Deployment Revision Before Rollout:** `2`
* **Deployment Revision After Rollout:** `3`
* **Probe Window (UTC):** `2026-11-13T19:14:59.106636Z` to `2026-11-13T19:15:29.119069Z`
* **Probe Interval / Duration:** `0.25s` interval over `30s`
* **CSV Output:** `docs/rolling_update_probe.csv` (`121` lines: `1` header + `120` recorded samples)
* **Availability Result:** `120` successful HTTP `200` responses; `0` failed readiness requests (`100%` availability)
* **Observed Latency Range (head/tail samples):** `~2.21 ms` to `~27.32 ms`

---

## Part B — HTTP Sampling & p95 Latency

* **Endpoint:** `/health/ready`
* **Environment:** Local Kubernetes cluster (`kind-fulfillops`)
* **Total requests:** `200`
* **Successful requests:** `200` (HTTP `200`)
* **Failed requests:** `0`
* **p95 latency:** `5.252 ms`
* **CSV Output:** `docs/http_samples.csv` (`201` lines: `1` header + `200` samples)
* **Limitations:** Measurements were taken locally, sequentially, without production network latency or concurrent production traffic.