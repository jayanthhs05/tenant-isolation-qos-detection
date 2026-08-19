#!/usr/bin/env bash
# Issues a raw curl request authenticated as a specific tenant ServiceAccount's own token,
# bypassing kubectl entirely. Needed because `kubectl ... --token=X` does NOT reliably override
# an existing kubeconfig context's client-certificate auth (see docs/risk-log.md #12) — it was
# found, while manually testing the control-plane monitor, to silently fall back to the ambient
# kubernetes-admin identity instead of the token, making it useless for scenarios that need to
# prove what a specific tenant identity can or cannot do.
#
# Usage: curl_as_sa.sh <namespace> <serviceaccount> <api-path> [extra curl args...]
# Example: curl_as_sa.sh tenant-a tenant-a-app /api/v1/namespaces/tenant-b/pods
set -euo pipefail

NAMESPACE="$1"
SERVICE_ACCOUNT="$2"
API_PATH="$3"
shift 3

SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA_FILE=$(mktemp)
trap 'rm -f "$CA_FILE"' EXIT
kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
  | base64 -d > "$CA_FILE"

TOKEN=$(kubectl create token "$SERVICE_ACCOUNT" -n "$NAMESPACE" --duration=10m)

curl -s -o /dev/null -w "%{http_code}\n" --cacert "$CA_FILE" \
  -H "Authorization: Bearer $TOKEN" \
  "${SERVER}${API_PATH}" "$@"
