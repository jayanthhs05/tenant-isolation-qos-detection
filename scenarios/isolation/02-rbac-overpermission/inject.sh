#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Granting tenant-a's ServiceAccount 'view' access in tenant-b (over-permissioning mistake)"
kubectl create rolebinding tenant-a-overpermission --clusterrole=view \
  --serviceaccount=tenant-a:tenant-a-app -n tenant-b --dry-run=client -o yaml | kubectl apply -f -

echo ">>> Exercising the newly (mis)granted access as tenant-a-app"
STATUS=$(bash "$ROOT_DIR/scenarios/driver/curl_as_sa.sh" tenant-a tenant-a-app /api/v1/namespaces/tenant-b/pods)
echo "HTTP status: $STATUS (expected 200 — access is now genuinely, but wrongly, permitted)"
