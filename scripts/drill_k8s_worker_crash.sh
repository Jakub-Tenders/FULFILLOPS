#!/usr/bin/env bash
# Worker-kill drill on the kind cluster (isolated test data only).
# Needs WORKER_PROCESS_DELAY_SECONDS>=8 committed in deploy/k8s/configmap.yaml and synced, so orders stay "processing" long enough.
set -euo pipefail
BASE="${BASE_URL:-http://localhost:8080}"
NS=fulfillops
TS=$(date +%s)
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
post() {
  curl -s -X POST "$BASE/api/v1/orders" -H 'Content-Type: application/json' -H "Idempotency-Key: drill-$TS-$1" \
    -d '{"customer_id":"CUST-DRILL","items":[{"sku":"PART-200","quantity":1}]}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["order_id"])'
}
status() {
  curl -s "$BASE/api/v1/orders/$1" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])'
}

log "workers before:"; kubectl -n "$NS" get pods -l app=fulfillops-worker
A=$(post 1); B=$(post 2)
log "orders: $A $B"
sleep 3
log "statuses: $(status "$A") $(status "$B")"
VICTIM=$(kubectl -n "$NS" get pods -l app=fulfillops-worker -o name | head -1)
log "killing $VICTIM mid-processing"
kubectl -n "$NS" delete "$VICTIM" --grace-period=0 --force
for i in $(seq 1 60); do
  SA=$(status "$A"); SB=$(status "$B")
  log "poll $i: $SA $SB"
  if [ "$SA" = "completed" ] && [ "$SB" = "completed" ]; then
    log "both orders completed after worker crash"; kubectl -n "$NS" get pods -l app=fulfillops-worker; exit 0
  fi
  sleep 3
done
log "FAILED: orders not completed in time"; exit 1
