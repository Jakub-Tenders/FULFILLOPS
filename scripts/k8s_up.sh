#!/usr/bin/env bash
# kind cluster + image build/load + direct apply (no Argo). Usage: scripts/k8s_up.sh [tag]
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER=fulfillops
NS=fulfillops
TAG="${1:-$(git rev-parse --short HEAD)}"

for bin in docker kind kubectl perl openssl; do
  command -v "$bin" >/dev/null || { echo "missing: $bin"; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "docker daemon not running"; exit 1; }
if lsof -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1 && ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "port 8080 is busy (docker compose still up?). Run: docker compose down"; exit 1
fi

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --config deploy/kind/kind-config.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

./scripts/sync_k8s_sql.sh

docker build -t "fulfillops:$TAG" .
kind load docker-image "fulfillops:$TAG" --name "$CLUSTER" || {
  echo "kind load failed, falling back to image-archive (Docker Desktop containerd store issue)"
  docker save "fulfillops:$TAG" -o "/tmp/fulfillops-$TAG.tar"
  kind load image-archive "/tmp/fulfillops-$TAG.tar" --name "$CLUSTER"
}

perl -0pi -e "s/newTag: .*/newTag: $TAG/" deploy/k8s/kustomization.yaml
echo "image tag set to $TAG in deploy/k8s/kustomization.yaml (commit it to deploy via Argo)"

kubectl apply -f deploy/k8s/namespace.yaml
./scripts/create_k8s_secret.sh
kubectl apply -k deploy/k8s
kubectl -n "$NS" rollout status statefulset/postgres --timeout=180s
kubectl -n "$NS" rollout status deploy/fulfillops-api --timeout=180s
kubectl -n "$NS" rollout status deploy/fulfillops-worker --timeout=180s
kubectl -n "$NS" get pods -o wide
echo "API on http://localhost:8080  (curl -i http://localhost:8080/health/ready)"
