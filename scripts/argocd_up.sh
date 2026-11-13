#!/usr/bin/env bash
# Installs Argo CD in kind and registers the Application.
# ARGO_REVISION=<branch> to test from a branch before merge (default main).
# ARGOCD_VERSION=vX.Y.Z to pin (default: stable; the script prints the version it got, record it in the report).
set -euo pipefail
cd "$(dirname "$0")/.."
REV="${ARGO_REVISION:-main}"
VER="${ARGOCD_VERSION:-stable}"
kubectl config use-context kind-fulfillops >/dev/null
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${VER}/manifests/install.yaml"
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s 2>/dev/null \
  || kubectl -n argocd rollout status deploy/argocd-application-controller --timeout=300s || true
sed "s|targetRevision: main|targetRevision: ${REV}|" deploy/argocd/fulfillops-app.yaml | kubectl apply -f -
echo "Argo CD image: $(kubectl -n argocd get deploy argocd-server -o jsonpath='{.spec.template.spec.containers[0].image}')"
echo "admin password: $(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
echo "UI: kubectl -n argocd port-forward svc/argocd-server 8443:443  ->  https://localhost:8443 (user admin)"
echo "status: kubectl -n argocd get application fulfillops"
