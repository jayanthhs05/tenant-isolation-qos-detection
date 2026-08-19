#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Probing tenant-b's Secrets as tenant-a's own ServiceAccount (no grant exists)"
STATUS=$(bash "$ROOT_DIR/scenarios/driver/curl_as_sa.sh" tenant-a tenant-a-app /api/v1/namespaces/tenant-b/secrets)
echo "HTTP status: $STATUS (expected 403 — RBAC correctly denies this; the attempt is still the signal)"
