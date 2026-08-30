#!/usr/bin/env bash
# Companion to scripts/up.sh. Stops the background monitors started by up.sh; with no flags
# also deletes the kind cluster (image build cache and bin/ tools are left alone — re-run
# scripts/up.sh to bring everything back).
#
# Usage:
#   scripts/down.sh                 # stop monitors + delete the kind cluster
#   scripts/down.sh --monitors-only # stop monitors, leave the cluster running
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"
LOG_DIR="${ROOT_DIR}/.run"
CLUSTER_NAME="${CLUSTER_NAME:-tenant-lab}"

MONITORS_ONLY=0
if [ "${1:-}" = "--monitors-only" ]; then
  MONITORS_ONLY=1
fi

echo ">>> Stopping monitors"
for name in control-plane-monitor network-flow-monitor resource-monitor; do
  pidfile="${LOG_DIR}/${name}.pid"
  if [ -f "${pidfile}" ]; then
    pid="$(cat "${pidfile}")"
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}"
      echo "    stopped ${name} (pid ${pid})"
    fi
    rm -f "${pidfile}"
  fi
done

if [ "${MONITORS_ONLY}" -eq 1 ]; then
  echo ">>> --monitors-only passed, leaving kind cluster '${CLUSTER_NAME}' running"
  exit 0
fi

if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo ">>> Deleting kind cluster '${CLUSTER_NAME}'"
  kind delete cluster --name "${CLUSTER_NAME}"
else
  echo ">>> No kind cluster named '${CLUSTER_NAME}' found, nothing to delete"
fi
