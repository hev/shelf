#!/usr/bin/env bash
# Deploy the Firn engine + shelf-on-Firn demo to EKS layer-prod (account 186219257916).
#
# Prereqs (one-time, see firn_demo/deploy/README.md): S3 bucket hev-firn-186219257916,
# IRSA role layer-prod-firn-sa-role, ECR repos firnflow-api + shelf-firn-demo, and the
# firn-keys Secret in the firn namespace.
#
# Usage:  ./firn_demo/deploy/deploy.sh          # build+push+apply+index
#         SKIP_BUILD=1 ./firn_demo/deploy/deploy.sh   # just (re)apply manifests
set -euo pipefail

PROFILE=mesh-916
REGION=us-east-1
ACCOUNT=186219257916
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
DEPOT_PROJECT=t4vlld595v          # hevmesh
FIRNFLOW_DIR="${FIRNFLOW_DIR:-$(cd "$(dirname "$0")/../../../firnflow" && pwd)}"
SHELF_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "==> ECR login"
aws ecr get-login-password --profile "$PROFILE" --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "==> build+push firnflow-api (from $FIRNFLOW_DIR)"
  # --platform pinned: the layer-prod nodes are amd64 (see the demo build below).
  ( cd "$FIRNFLOW_DIR" && depot build --platform linux/amd64 -t "$REGISTRY/firnflow-api:latest" --push . )

  echo "==> build+push shelf-firn-demo (from $SHELF_DIR)"
  # --platform pinned: the layer-prod nodes are amd64; without it depot defaults
  # to the build host's arch (arm64 on Apple Silicon) → ImagePullBackOff.
  ( cd "$SHELF_DIR" && depot build --project "$DEPOT_PROJECT" --platform linux/amd64 \
      -f firn_demo/Dockerfile -t "$REGISTRY/shelf-firn-demo:latest" --push . )
fi

echo "==> apply manifests"
kubectl apply -f "$SHELF_DIR/firn_demo/deploy/00-namespace-sa.yaml"
kubectl -n firn get secret firn-keys >/dev/null 2>&1 || {
  echo "!! firn-keys Secret missing — create it (see secret.example.yaml) then re-run." >&2
  exit 1
}
kubectl apply -f "$SHELF_DIR/firn_demo/deploy/10-firn-engine.yaml"
kubectl apply -f "$SHELF_DIR/firn_demo/deploy/20-demo.yaml"
kubectl apply -f "$SHELF_DIR/firn_demo/deploy/40-ingress.yaml"

echo "==> wait for engine + demo"
kubectl -n firn rollout status deploy/firn --timeout=300s
kubectl -n firn rollout status deploy/shelf-firn-demo --timeout=300s

echo "==> (re)run the indexer Job"
kubectl -n firn delete job shelf-firn-indexer --ignore-not-found
kubectl apply -f "$SHELF_DIR/firn_demo/deploy/30-indexer-job.yaml"
kubectl -n firn wait --for=condition=complete job/shelf-firn-indexer --timeout=1200s

echo "==> done. firn.hevlayer.com (ensure the Route53 record points at the hev-public ALB)."
