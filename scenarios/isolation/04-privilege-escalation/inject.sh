#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Escalating tenant-a's ServiceAccount to cluster-admin (worst-case over-permissioning)"
kubectl create clusterrolebinding tenant-a-privesc --clusterrole=cluster-admin \
  --serviceaccount=tenant-a:tenant-a-app --dry-run=client -o yaml | kubectl apply -f -

echo ">>> Abusing the escalated privilege against both other tenants"
STATUS_B=$(bash "$ROOT_DIR/scenarios/driver/curl_as_sa.sh" tenant-a tenant-a-app /api/v1/namespaces/tenant-b/pods)
echo "tenant-b HTTP status: $STATUS_B (expected 200)"
STATUS_C=$(bash "$ROOT_DIR/scenarios/driver/curl_as_sa.sh" tenant-a tenant-a-app /api/v1/namespaces/tenant-c/pods)
echo "tenant-c HTTP status: $STATUS_C (expected 200)"
