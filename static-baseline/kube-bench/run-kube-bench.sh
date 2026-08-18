#!/usr/bin/env bash
# Phase 4: static CIS baseline, representing the "static-only" comparison point in the
# evaluation (proposal §5.2 / docs/implementation-plan.md).
#
# kind's control-plane node is a real machine as far as kube-bench is concerned (kubeadm
# static-pod manifests, kubelet config, PKI — all at their standard filesystem paths), just
# running inside a container instead of on bare metal — so rather than fighting a Job +
# hostPath-mounts setup (the usual source of kube-bench-on-kind flakiness, see
# docs/risk-log.md #2), this copies the kube-bench binary + its cfg/ (CIS check definitions)
# straight onto the node via `docker cp` and runs it there via `docker exec`. One run against
# the control-plane node covers all 5 categories (master, etcd, controlplane, node, policies)
# since kind's control-plane also runs its own kubelet — see the run's `Controls[].node_type`
# in results.json.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
OUT_DIR="${ROOT_DIR}/static-baseline/kube-bench"
CLUSTER_NAME="tenant-lab"
NODE="${CLUSTER_NAME}-control-plane"

if [ ! -x "${BIN_DIR}/kube-bench" ] || [ ! -d "${BIN_DIR}/kube-bench-cfg" ]; then
  echo "kube-bench (or its cfg/ dir) not found in ${BIN_DIR} — run scripts/install-tools.sh first." >&2
  exit 1
fi

echo ">>> Copying kube-bench onto ${NODE}"
docker cp "${BIN_DIR}/kube-bench" "${NODE}:/usr/local/bin/kube-bench"
docker exec "${NODE}" chmod +x /usr/local/bin/kube-bench
docker cp "${BIN_DIR}/kube-bench-cfg" "${NODE}:/kube-bench-cfg"

echo ">>> Running kube-bench (human-readable)"
docker exec "${NODE}" sh -c 'kube-bench run --config-dir /kube-bench-cfg' > "${OUT_DIR}/results.txt" 2>&1 || true

echo ">>> Running kube-bench (JSON)"
docker exec "${NODE}" sh -c 'kube-bench run --config-dir /kube-bench-cfg --json' > "${OUT_DIR}/results.json" 2>/dev/null || true

echo ">>> Summary:"
tail -n 12 "${OUT_DIR}/results.txt"
echo ">>> Full results: ${OUT_DIR}/results.txt and ${OUT_DIR}/results.json"
