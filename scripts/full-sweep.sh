#!/usr/bin/env bash
# One command for a full evaluation sweep: brings the environment up, starts the victim-side
# k6/QoS load, runs evaluation/harness/run_evaluation.py, then scores it with
# evaluation/metrics/compute_metrics.py. Chains the existing scripts (scripts/up.sh,
# scripts/run-fairness-load.sh) rather than reimplementing them — see COMMANDS.md and
# docs/evaluation-methodology.md for the manual equivalent.
#
# Any extra args are passed straight through to run_evaluation.py, e.g.:
#   scripts/full-sweep.sh --shuffle --seed 1
#   scripts/full-sweep.sh --dry-run                 # just prints the plan, skips all setup
#   scripts/full-sweep.sh --scenarios scenarios/isolation/01-networkpolicy-misconfig \
#       --repetitions 1 --negative-control-windows 1
#
# Configurable via env vars (defaults shown):
#   VICTIM_TENANT=tenant-b   tenant k6 loads for the victim-side QoS signal (no fairness
#                            scenario injects into it directly)
#   LOAD_DURATION=60m        k6 run length — must outlast the whole sweep (~35 min default)
#   QOS_BASELINE_SECONDS=10  qos_monitor.py baseline window
#   SETTLE_SECONDS=15        wait after starting load, so the QoS baseline settles on quiet
#                            traffic before the first scenario runs
#   KEEP_UP=1                1 = leave cluster + monitors running afterwards; 0 = scripts/down.sh
#   SCORE=1                  1 = run compute_metrics.py at the end; 0 = stop after the sweep
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"
LOG_DIR="${ROOT_DIR}/.run"
RESULTS_DIR="${ROOT_DIR}/evaluation/results"

VICTIM_TENANT="${VICTIM_TENANT:-tenant-b}"
LOAD_DURATION="${LOAD_DURATION:-60m}"
QOS_BASELINE_SECONDS="${QOS_BASELINE_SECONDS:-10}"
SETTLE_SECONDS="${SETTLE_SECONDS:-15}"
KEEP_UP="${KEEP_UP:-1}"
SCORE="${SCORE:-1}"
PYTHON="${PYTHON:-python3}"

# --dry-run: no cluster time, no setup, no teardown — just show the plan.
for arg in "$@"; do
  if [ "${arg}" = "--dry-run" ]; then
    exec "${PYTHON}" "${ROOT_DIR}/evaluation/harness/run_evaluation.py" "$@"
  fi
done

LOAD_STARTED=0
cleanup() {
  if [ "${LOAD_STARTED}" -eq 1 ]; then
    echo
    echo "=== [cleanup] stopping k6 / qos_monitor / port-forward ==="
    "${ROOT_DIR}/scripts/run-fairness-load.sh" stop || true
  fi
  if [ "${KEEP_UP}" -eq 0 ]; then
    echo "=== [cleanup] KEEP_UP=0 -> scripts/down.sh --monitors-only ==="
    "${ROOT_DIR}/scripts/down.sh" --monitors-only || true
  fi
}
trap cleanup EXIT

echo "=== [1/6] Bringing the environment up (scripts/up.sh) ==="
"${ROOT_DIR}/scripts/up.sh"

echo
echo "=== [2/6] Verifying tenant namespaces and monitors ==="
read -ra TENANTS <<< "${TENANTS:-tenant-a tenant-b tenant-c}"
for tenant in "${TENANTS[@]}"; do
  if ! kubectl get namespace "${tenant}" >/dev/null 2>&1; then
    echo "Namespace ${tenant} still missing after scripts/up.sh — check its output above." >&2
    exit 1
  fi
done
echo "    namespaces: ${TENANTS[*]} present"

sleep 5  # give the just-started monitors a moment to fail if they're going to
for name in control-plane-monitor network-flow-monitor resource-monitor; do
  pidfile="${LOG_DIR}/${name}.pid"
  if [ ! -f "${pidfile}" ] || ! kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "Monitor ${name} is not running. Last 20 lines of ${LOG_DIR}/${name}.log:" >&2
    tail -n 20 "${LOG_DIR}/${name}.log" >&2 || true
    exit 1
  fi
  echo "    ${name}: running (pid $(cat "${pidfile}"))"
done

echo
echo "=== [3/6] Starting victim-side load against ${VICTIM_TENANT} (${LOAD_DURATION}) ==="
TENANT="${VICTIM_TENANT}" DURATION="${LOAD_DURATION}" BASELINE_SECONDS="${QOS_BASELINE_SECONDS}" \
  "${ROOT_DIR}/scripts/run-fairness-load.sh"
LOAD_STARTED=1
echo "    waiting ${SETTLE_SECONDS}s for the QoS baseline to settle"
sleep "${SETTLE_SECONDS}"

echo
echo "=== [4/6] Archiving any previous sweep results ==="
stamp="$(date +%Y%m%d-%H%M%S)"
for f in ground_truth.jsonl negative_controls.jsonl; do
  if [ -s "${RESULTS_DIR}/${f}" ]; then
    mv "${RESULTS_DIR}/${f}" "${RESULTS_DIR}/${f}.bak-${stamp}"
    echo "    ${f} -> ${f}.bak-${stamp}"
  fi
done

echo
echo "=== [5/6] Running the sweep (evaluation/harness/run_evaluation.py $*) ==="
"${PYTHON}" "${ROOT_DIR}/evaluation/harness/run_evaluation.py" "$@"

echo
if [ "${SCORE}" -eq 1 ]; then
  echo "=== [6/6] Scoring (evaluation/metrics/compute_metrics.py) ==="
  "${PYTHON}" "${ROOT_DIR}/evaluation/metrics/compute_metrics.py"
else
  echo "=== [6/6] SCORE=0 -> skipping compute_metrics.py ==="
  echo "    score later with: ${PYTHON} evaluation/metrics/compute_metrics.py"
fi

echo
echo "=== Done. Results in ${RESULTS_DIR}/ (metrics.json, ground_truth.jsonl, negative_controls.jsonl) ==="
if [ "${KEEP_UP}" -eq 1 ]; then
  echo "Cluster + monitors left running. Tear down with: scripts/down.sh"
fi
