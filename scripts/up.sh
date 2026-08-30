#!/usr/bin/env bash
# One-shot bring-up: runs Phase 0-4 in order (tool install -> kind cluster w/ audit logging ->
# Cilium/Hubble CNI -> per-tenant namespaces/RBAC/NetworkPolicy/ResourceQuota -> demo app), then
# starts all three monitors in the background. Idempotent — safe to re-run; each underlying
# script skips work that's already done.
#
# What this does NOT do (see COMMANDS.md for these):
#   - inject scenarios (scenarios/driver/run_scenario.py)
#   - run the k6 load generator / victim-side QoS monitor
#   - run fusion or the evaluation harness
# those are per-experiment actions with their own parameters, not part of "bring the
# environment up".
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"
LOG_DIR="${ROOT_DIR}/.run"
mkdir -p "${LOG_DIR}"

echo "=== [1/6] Installing CLI tools into bin/ ==="
"${ROOT_DIR}/scripts/install-tools.sh"

echo "=== [2/6] Creating kind cluster (audit logging, no default CNI) ==="
"${ROOT_DIR}/scripts/bootstrap-cluster.sh"

echo "=== [3/6] Installing Cilium + Hubble ==="
"${ROOT_DIR}/scripts/install-cilium.sh"

echo "=== [4/6] Applying tenant namespaces (namespace, RBAC, NetworkPolicy, ResourceQuota) ==="
read -ra TENANTS <<< "${TENANTS:-tenant-a tenant-b tenant-c}"
for TENANT in "${TENANTS[@]}"; do
  kubectl apply -f "${ROOT_DIR}/infra/namespaces/${TENANT}/"
done

echo "=== [5/6] Building and deploying the demo app into every tenant namespace ==="
"${ROOT_DIR}/scripts/deploy-app.sh"

echo "=== [6/6] Starting monitors in the background (logs in ${LOG_DIR}/) ==="
PYTHON="${PYTHON:-python3}"
if ! "${PYTHON}" -c "import yaml" >/dev/null 2>&1; then
  echo ">>> Installing scenarios/requirements.txt (PyYAML) for the current Python interpreter"
  "${PYTHON}" -m pip install -r "${ROOT_DIR}/scenarios/requirements.txt"
fi

start_monitor() {
  local name="$1"; shift
  local pidfile="${LOG_DIR}/${name}.pid"
  if [ -f "${pidfile}" ] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo ">>> ${name} already running (pid $(cat "${pidfile}"))"
    return
  fi
  echo ">>> Starting ${name}"
  nohup "${PYTHON}" "$@" >"${LOG_DIR}/${name}.log" 2>&1 &
  echo $! > "${pidfile}"
}

start_monitor control-plane-monitor "${ROOT_DIR}/monitors/control-plane-monitor/monitor.py"
start_monitor network-flow-monitor "${ROOT_DIR}/monitors/network-flow-monitor/monitor.py"
start_monitor resource-monitor "${ROOT_DIR}/monitors/resource-monitor/monitor.py"

echo
echo "=== Up. Cluster: tenant-lab | Monitors: control-plane, network-flow, resource (background) ==="
echo "Tail monitor output:   tail -f ${LOG_DIR}/*.log"
echo "Stop monitors:         scripts/down.sh --monitors-only"
echo "Tear everything down:  scripts/down.sh"
echo "See COMMANDS.md for scenario injection, load generation, fusion, and evaluation commands."
