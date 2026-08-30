#!/usr/bin/env bash
# One command to get three screenshot-able outputs open at once, for a report/slide deck:
#   1. terminal: re-runs fusion + compute_metrics.py and prints the alerts + metrics table
#   2. browser:  Hubble UI (live tenant-to-tenant network flow graph)
#   3. browser:  the demo app's Swagger docs (/docs) for one tenant
# Assumes scripts/up.sh has already been run (cluster + monitors already up) — this only
# reads existing state and opens read-only views, it doesn't inject anything itself.
#
# Usage:
#   scripts/demo-screenshots.sh          # print metrics, open both UIs
#   scripts/demo-screenshots.sh stop     # stop the port-forwards this started
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
export PATH="${BIN_DIR}:${PATH}"
LOG_DIR="${ROOT_DIR}/.run"
mkdir -p "${LOG_DIR}"

TENANT="${TENANT:-tenant-a}"
HUBBLE_UI_PORT="${HUBBLE_UI_PORT:-12000}"
APP_DOCS_PORT="${APP_DOCS_PORT:-18000}"

NAMES=(port-forward-hubble-ui port-forward-app-docs)

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

stop_all  # avoid stacking a second set of port-forwards on top

open_browser() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${url}" >/dev/null 2>&1 &
  else
    echo "    (open manually: ${url})"
  fi
}

echo "=== 1/3: fusion + metrics (screenshot this terminal output) ==="
python3 "${ROOT_DIR}/fusion/src/run_fusion.py"
echo
python3 "${ROOT_DIR}/evaluation/metrics/compute_metrics.py"

echo
echo "=== 2/3: Hubble UI (network flow graph) -> http://localhost:${HUBBLE_UI_PORT} ==="
nohup kubectl -n kube-system port-forward svc/hubble-ui "${HUBBLE_UI_PORT}:80" \
  >"${LOG_DIR}/port-forward-hubble-ui.log" 2>&1 &
echo $! > "${LOG_DIR}/port-forward-hubble-ui.pid"
disown

echo "=== 3/3: ${TENANT}/notes-api Swagger docs -> http://localhost:${APP_DOCS_PORT}/docs ==="
nohup kubectl -n "${TENANT}" port-forward svc/notes-api "${APP_DOCS_PORT}:80" \
  >"${LOG_DIR}/port-forward-app-docs.log" 2>&1 &
echo $! > "${LOG_DIR}/port-forward-app-docs.pid"
disown

sleep 2
open_browser "http://localhost:${HUBBLE_UI_PORT}"
open_browser "http://localhost:${APP_DOCS_PORT}/docs"

echo
echo "Stop both port-forwards: scripts/demo-screenshots.sh stop"
