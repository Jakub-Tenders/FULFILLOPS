#!/usr/bin/env bash
# Copies schema + seed (same two files compose.yaml mounts) into deploy/k8s/sql/ so kustomize/Argo can read them.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p deploy/k8s/sql
cp db/migrations/001_initial_schema.sql deploy/k8s/sql/001_initial_schema.sql
cp db/seed.sql deploy/k8s/sql/002_seed.sql
echo "synced SQL -> deploy/k8s/sql/ (commit these two files)"
