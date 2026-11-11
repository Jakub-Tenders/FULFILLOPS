#!/usr/bin/env bash
# Worker crash drill (isolated local lab only; resets the *_test database).
# Needs: Compose stack running (docker compose up -d --build --wait --scale worker=2)
# Output: docs/evidence/crash_recovery.txt
set -euo pipefail

export APP_ENV=test
export DATABASE_URL="postgresql+psycopg://fulfillops:fulfillops@localhost:5432/fulfillops_test"
OUT="docs/evidence/crash_recovery.txt"
API="http://localhost:8080"
PSQL=(docker compose exec -T postgres psql -U fulfillops -d fulfillops_test -c)

mkdir -p docs/evidence
: > "$OUT"
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$OUT"; }
show() { "${PSQL[@]}" "select work_id, status, attempts, claimed_at is not null as claimed from work_items order by work_id" | tee -a "$OUT"; }

log "1. reset the disposable test DB"
python scripts/reset_test_data.py | tee -a "$OUT"

log "2. restart workers with a 10 s processing delay (2 workers) so a job is in flight"
WORKER_PROCESS_DELAY_SECONDS=10 docker compose up -d --force-recreate --no-deps --scale worker=2 worker >/dev/null 2>&1
sleep 4

log "3. create 4 orders (each worker will hold one job for 10 s)"
for i in 1 2 3 4; do
  curl -s -X POST "$API/api/v1/orders" \
    -H 'Content-Type: application/json' -H "Idempotency-Key: crash-drill-00$i" \
    -d '{"customer_id":"CRASH-DRILL","items":[{"sku":"KIT-100","quantity":1}]}' >/dev/null
done
sleep 3
log "4. work items while both workers are busy"
show

log "5. KILL worker 1 mid-processing (docker kill = SIGKILL, no cleanup)"
docker kill fulfillops-worker-1 | tee -a "$OUT"
sleep 2
log "6. state right after the crash (one job is stuck in 'processing', its lease expires after 30 s)"
show

log "7. waiting for recovery (the surviving worker finishes its jobs, then reclaims the stale one)"
for _ in $(seq 1 24); do
  pending=$("${PSQL[@]}" "select count(*) from work_items where status <> 'completed'" -t -A)
  [ "$pending" = "0" ] && break
  sleep 5
done

log "8. final state: every order completed; the abandoned job shows attempts = 2"
show
"${PSQL[@]}" "select o.status as order_status, count(*) from orders o group by o.status" | tee -a "$OUT"
"${PSQL[@]}" "select count(*) as completed_jobs, count(*) filter (where attempts = 1) as attempts_1, count(*) filter (where attempts = 2) as attempts_2 from work_items where status = 'completed'" | tee -a "$OUT"

log "9. restore normal worker settings (no delay, 2 workers)"
WORKER_PROCESS_DELAY_SECONDS=0 docker compose up -d --force-recreate --no-deps --scale worker=2 worker >/dev/null 2>&1
log "done; evidence saved to $OUT"