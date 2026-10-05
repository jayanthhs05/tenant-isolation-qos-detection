#!/usr/bin/env bash
# Wraps the victim-side QoS load generation steps from COMMANDS.md into one configurable
# command: port-forwards a victim tenant's notes-api Service, runs k6 steady load against it,
# and tails that output with qos_monitor.py so fusion's fairness rule has the independent
# victim-side breach signal it needs (see monitors/resource-monitor/qos_monitor.py). All three
# processes run in the background so this returns immediately; run alongside a fairness
# scenario injection (scripts/driver/run_scenario.py) or evaluation/harness/run_evaluation.py.
#
# Configurable via env vars (defaults shown):
#   TENANT=tenant-b VUS=2 DURATION=4m LOCAL_PORT=18080 BREACH_RATIO=2.5 BASELINE_SECONDS=10
#
# Usage:
#   scripts/run-fairness-load.sh                                  # defaults
#   TENANT=tenant-c VUS=8 DURATION=6m scripts/run-fairness-load.sh # heavier/longer load
#   scripts/run-fairness-load.sh stop                              # stop everything this started
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"
LOG_DIR="${ROOT_DIR}/.run"
mkdir -p "${LOG_DIR}"

TENANT="${TENANT:-tenant-b}"
VUS="${VUS:-2}"
DURATION="${DURATION:-4m}"
LOCAL_PORT="${LOCAL_PORT:-18080}"
BREACH_RATIO="${BREACH_RATIO:-2.5}"
BASELINE_SECONDS="${BASELINE_SECONDS:-10}"
K6_OUTPUT="${LOG_DIR}/k6-${TENANT}.jsonl"

NAMES=(port-forward-fairness-load k6-fairness-load qos-monitor-fairness-load)

stop_all() {
  for name in "${NAMES[@]}"; do
    pidfile="${LOG_DIR}/${name}.pid"
    if [ -f "${pidfile}" ]; then
      pid="$(cat "${pidfile}")"
      if kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}"
        echo ">>> stopped ${name} (pid ${pid})"
      fi
      rm -f "${pidfile}"
    fi
  done
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  exit 0
fi

stop_all  # avoid stacking a second run on top of one still in progress

if ! kubectl get namespace "${TENANT}" >/dev/null 2>&1; then
  echo "Namespace ${TENANT} does not exist — scripts/up.sh (or apply infra/namespaces/${TENANT}/) first." >&2
  exit 1
fi

echo ">>> Port-forwarding ${TENANT}/notes-api to localhost:${LOCAL_PORT}"
nohup kubectl -n "${TENANT}" port-forward svc/notes-api "${LOCAL_PORT}:80" \
  >"${LOG_DIR}/port-forward-fairness-load.log" 2>&1 &
echo $! > "${LOG_DIR}/port-forward-fairness-load.pid"
disown

for i in $(seq 1 15); do
  if curl -sf "http://localhost:${LOCAL_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -sf "http://localhost:${LOCAL_PORT}/healthz" >/dev/null || {
  echo "port-forward to ${TENANT}/notes-api never became healthy" >&2
  exit 1
}

echo ">>> Starting k6 (${VUS} VUs, ${DURATION}) against ${TENANT}, output -> ${K6_OUTPUT}"
TARGET_URL="http://localhost:${LOCAL_PORT}/healthz" VUS="${VUS}" DURATION="${DURATION}" \
  nohup k6 run --out "json=${K6_OUTPUT}" "${ROOT_DIR}/load/k6/tenant-load-script.js" \
  >"${LOG_DIR}/k6-fairness-load.log" 2>&1 &
echo $! > "${LOG_DIR}/k6-fairness-load.pid"
disown

echo ">>> Starting qos_monitor.py (breach-ratio=${BREACH_RATIO}, baseline-seconds=${BASELINE_SECONDS})"
nohup python3 -u "${ROOT_DIR}/monitors/resource-monitor/qos_monitor.py" \
  --k6-output "${K6_OUTPUT}" --tenant "${TENANT}" \
  --breach-ratio "${BREACH_RATIO}" --baseline-seconds "${BASELINE_SECONDS}" \
  >"${LOG_DIR}/qos-monitor-fairness-load.log" 2>&1 &
echo $! > "${LOG_DIR}/qos-monitor-fairness-load.pid"
disown

echo
echo "=== Fairness load running against ${TENANT} for ${DURATION} ==="
echo "Stop early:  scripts/run-fairness-load.sh stop"
echo "Logs:        tail -f ${LOG_DIR}/{port-forward,k6,qos-monitor}-fairness-load.log"
