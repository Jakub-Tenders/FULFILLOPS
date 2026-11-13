# Kubernetes / GitOps (kind + Argo CD)

Prereqs (macOS arm64): docker, kind, kubectl, perl, openssl. Stop compose first (`docker compose down`): kind binds localhost:8080.

```bash
./scripts/k8s_up.sh                  # cluster, build, kind load, secret, kubectl apply -k
curl -i http://localhost:8080/health/ready
git add deploy scripts && git commit -m "k8s: deploy <sha>" && git push
./scripts/argocd_up.sh               # ARGO_REVISION=<branch> to test before merge
kubectl -n argocd get application fulfillops
./scripts/k8s_down.sh                # teardown
```

Deploying = change `images.newTag` in `deploy/k8s/kustomization.yaml`, commit, push. The Secret `fulfillops-db` is created out of Git by `scripts/create_k8s_secret.sh`.
Host access is via NodePort 30080 mapped to localhost:8080 (stable during rollouts, unlike `kubectl port-forward`).
