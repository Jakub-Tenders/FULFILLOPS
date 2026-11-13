#!/usr/bin/env bash
# Creates Secret fulfillops-db OUT OF GIT. Idempotent: never rotates an existing secret
# (the Postgres PVC keeps the first password, so rotating would break the DB login).
set -euo pipefail
NS=fulfillops
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
if kubectl -n "$NS" get secret fulfillops-db >/dev/null 2>&1; then
  echo "secret fulfillops-db already exists, leaving it untouched"
  exit 0
fi
PW="${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"
kubectl -n "$NS" create secret generic fulfillops-db \
  --from-literal=POSTGRES_PASSWORD="$PW" \
  --from-literal=DATABASE_URL="postgresql+psycopg://fulfillops:${PW}@postgres:5432/fulfillops_test"
echo "created secret fulfillops-db"
