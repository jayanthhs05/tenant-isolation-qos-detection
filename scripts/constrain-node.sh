#!/usr/bin/env bash
# Pins the kind worker node(s) to a small cpuset so fairness scenarios create genuine CPU
# contention. On a 16-core host a few CFS-limited hog pods can't hurt a co-located victim, so
# no victim-side QoS breach ever fires (docs/risk-log.md #15). Idempotent.
#
#   scripts/constrain-node.sh            # pin workers to CPUS (default 0-1)
#   scripts/constrain-node.sh release    # remove the pin
#   CPUS=0-3 scripts/constrain-node.sh
set -euo pipefail
CLUSTER_NAME="${CLUSTER_NAME:-tenant-lab}"
CPUS="${CPUS:-0-1}"
[ "${1:-}" = "release" ] && CPUS=""
for node in $(docker ps --format '{{.Names}}' | grep -E "^${CLUSTER_NAME}-worker[0-9]*$"); do
  docker update --cpuset-cpus "${CPUS}" "${node}" >/dev/null
  echo ">>> ${node}: cpuset=${CPUS:-<unrestricted>}"
done
