#!/usr/bin/env bash
# Phase 1: creates the kind cluster with Kubernetes audit logging wired up and the default
# CNI disabled (Cilium is installed separately by scripts/install-cilium.sh). Idempotent:
# if a cluster with this name already exists, it's left as-is.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"

CLUSTER_NAME="tenant-lab"
AUDIT_HOST_DIR="${ROOT_DIR}/infra/kind/audit"
RESOLV_CLEAN_PATH="${ROOT_DIR}/infra/kind/resolv-clean.conf"

if ! command -v kind >/dev/null 2>&1 || ! command -v kubectl >/dev/null 2>&1; then
  echo "kind/kubectl not found on PATH — run scripts/install-tools.sh first." >&2
  exit 1
fi

mkdir -p "${AUDIT_HOST_DIR}"
if [ ! -f "${AUDIT_HOST_DIR}/audit-policy.yaml" ]; then
  echo "Missing ${AUDIT_HOST_DIR}/audit-policy.yaml — kubeadm reads this at apiserver" >&2
  echo "startup, so it must exist before cluster creation." >&2
  exit 1
fi

if [ ! -f "${RESOLV_CLEAN_PATH}" ]; then
  echo "Missing ${RESOLV_CLEAN_PATH} — needed to keep the host's DNS search domain from" >&2
  echo "leaking into every pod (see docs/risk-log.md #11)." >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo ">>> kind cluster '${CLUSTER_NAME}' already exists, skipping creation"
else
  echo ">>> Rendering kind config with AUDIT_HOST_DIR=${AUDIT_HOST_DIR}, RESOLV_CLEAN_PATH=${RESOLV_CLEAN_PATH}"
  AUDIT_HOST_DIR="${AUDIT_HOST_DIR}" RESOLV_CLEAN_PATH="${RESOLV_CLEAN_PATH}" \
    envsubst < "${ROOT_DIR}/infra/kind/kind-config.yaml.tmpl" \
    > "${ROOT_DIR}/infra/kind/kind-config.rendered.yaml"

  echo ">>> Creating kind cluster '${CLUSTER_NAME}' (default CNI disabled; install Cilium next)"
  kind create cluster --name "${CLUSTER_NAME}" --config "${ROOT_DIR}/infra/kind/kind-config.rendered.yaml"
fi

echo ">>> Nodes (expect NotReady until Cilium is installed — run scripts/install-cilium.sh next):"
kubectl get nodes -o wide

# The apiserver static pod writes audit.log as root inside the control-plane container; on the
# host (bind mount) that's root:root mode 600 — unreadable to our user even though the file
# lives under our own project directory. `docker exec ... chmod` fixes this from inside the
# container (root chmod'ing its own file), which changes host-side perms too since it's the
# same inode — no sudo needed. See docs/risk-log.md #9.
echo ">>> Waiting for audit.log to appear inside the control-plane container, then relaxing its permissions"
for i in $(seq 1 30); do
  if docker exec "${CLUSTER_NAME}-control-plane" test -f /etc/kubernetes/audit/audit.log 2>/dev/null; then
    docker exec "${CLUSTER_NAME}-control-plane" chmod 644 /etc/kubernetes/audit/audit.log
    break
  fi
  sleep 1
done

echo ">>> Audit log path on host: ${AUDIT_HOST_DIR}/audit.log"
