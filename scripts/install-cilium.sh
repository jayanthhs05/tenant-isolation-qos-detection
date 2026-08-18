#!/usr/bin/env bash
# Installs Cilium (CNI) + Hubble onto the kind cluster created by bootstrap-cluster.sh, using
# the pinned chart version and values in infra/cilium/values.yaml. Idempotent via `helm upgrade
# --install`.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"

CILIUM_CHART_VERSION="1.16.2"
CLUSTER_NAME="tenant-lab"

for c in kubectl helm cilium hubble; do
  if ! command -v "$c" >/dev/null 2>&1; then
    echo "$c not found on PATH — run scripts/install-tools.sh first." >&2
    exit 1
  fi
done

if ! kubectl config current-context | grep -q "kind-${CLUSTER_NAME}"; then
  kubectl config use-context "kind-${CLUSTER_NAME}"
fi

echo ">>> Adding/updating cilium helm repo"
helm repo add cilium https://helm.cilium.io/ >/dev/null 2>&1 || true
helm repo update cilium >/dev/null

echo ">>> Installing/upgrading Cilium ${CILIUM_CHART_VERSION} via Helm"
helm upgrade --install cilium cilium/cilium \
  --version "${CILIUM_CHART_VERSION}" \
  --namespace kube-system \
  --values "${ROOT_DIR}/infra/cilium/values.yaml"

echo ">>> Waiting for Cilium to report healthy (this can take a few minutes on first install)"
cilium status --wait

echo ">>> Nodes (should now be Ready):"
kubectl get nodes -o wide

echo ">>> Smoke test: port-forwarding Hubble relay and sampling live flows for 5s"
kubectl -n kube-system port-forward svc/hubble-relay 4245:80 >/tmp/hubble-port-forward.log 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 3
hubble observe --server localhost:4245 --last 5 || echo "(no flows yet — cluster may still be idle, this is OK)"
