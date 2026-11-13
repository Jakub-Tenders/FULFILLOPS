# FulfillOps — Observability & Availability Evidence

## Part A — Rolling Update Availability Test

* **Target Endpoint:** `http://localhost:8080/health/ready`
* **Environment:** Local Kubernetes cluster (`kind-fulfillops`, namespace `fulfillops`)
* **Deployment:** `deployment/fulfillops-api` (2 replicas)
* **Deployment Revision Before Rollout:** `1`
* **Deployment Revision After Rollout:** `2`
* **Probe Window (UTC):** `2026-11-13T19:15:37.232325Z` to `2026-11-13T19:16:07.299807Z`
* **Probe Interval / Duration:** `0.25s` interval over `30s`
* **Total Lines in `docs/rolling_update_probe.csv`:** `118` lines (`1` header + `117` recorded samples)
* **Availability Result:** `117` successful HTTP `200` responses; `0` failed readiness requests (`100%` availability)
* **Sample Response Latency Range (observed in head/tail):** `~2.16 ms` to `~11.61 ms`

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