#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Removing the oversized batch job"
kubectl delete pod quota-bypass-hog -n tenant-a --ignore-not-found --force --grace-period=0

echo ">>> Restoring tenant-a's original ResourceQuota and LimitRange"
kubectl apply -f "$ROOT_DIR/infra/namespaces/tenant-a/resourcequota.yaml"
