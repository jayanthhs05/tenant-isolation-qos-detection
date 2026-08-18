#!/usr/bin/env bash
# Builds the demo notes-api image, loads it into the kind cluster's nodes (kind clusters can't
# pull from a pod's node without a registry, so `kind load` is the standard workaround), and
# applies the per-tenant Deployment+Service (app/k8s/app.yaml.tmpl) into every tenant namespace
# created by infra/namespaces/*.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"

CLUSTER_NAME="tenant-lab"
IMAGE="tenant-lab/notes-api:dev"
TENANTS=(tenant-a tenant-b tenant-c)

echo ">>> Building ${IMAGE}"
docker build -t "${IMAGE}" -f "${ROOT_DIR}/app/Dockerfile" "${ROOT_DIR}/app/"

echo ">>> Loading image into kind cluster '${CLUSTER_NAME}'"
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"

for TENANT in "${TENANTS[@]}"; do
  if ! kubectl get namespace "${TENANT}" >/dev/null 2>&1; then
    echo "Namespace ${TENANT} does not exist — apply infra/namespaces/${TENANT}/ first." >&2
    exit 1
  fi
  echo ">>> Deploying notes-api into ${TENANT}"
  TENANT="${TENANT}" IMAGE="${IMAGE}" envsubst '${TENANT} ${IMAGE}' \
    < "${ROOT_DIR}/app/k8s/app.yaml.tmpl" | kubectl apply -f -
done

echo ">>> Waiting for rollouts"
for TENANT in "${TENANTS[@]}"; do
  kubectl -n "${TENANT}" rollout status deployment/notes-api --timeout=120s
done

echo ">>> Pods:"
kubectl get pods -A -l app=notes-api -o wide
