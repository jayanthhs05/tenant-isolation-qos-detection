#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Restoring tenant-a's and tenant-b's original NetworkPolicies"
kubectl apply -f "$ROOT_DIR/infra/namespaces/tenant-a/networkpolicy.yaml"
kubectl apply -f "$ROOT_DIR/infra/namespaces/tenant-b/networkpolicy.yaml"
