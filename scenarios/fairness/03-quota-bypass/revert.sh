#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

echo ">>> Removing the oversized batch pods"
remove_hogs

echo ">>> Restoring tenant-a's original ResourceQuota and LimitRange"
kubectl apply -f "$ROOT_DIR/infra/namespaces/tenant-a/resourcequota.yaml"
