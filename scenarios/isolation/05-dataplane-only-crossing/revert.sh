#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Restoring tenant-a's and tenant-c's original NetworkPolicies"
kubectl apply -f "$ROOT_DIR/infra/namespaces/tenant-a/networkpolicy.yaml"
kubectl apply -f "$ROOT_DIR/infra/namespaces/tenant-c/networkpolicy.yaml"
