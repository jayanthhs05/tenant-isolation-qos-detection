#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Broadening tenant-a's NetworkPolicy egress AND tenant-c's ingress to any tenant namespace"
# Both sides must be broadened — see the identical note in scenario 01's inject.sh
# (docs/risk-log.md #13): egress and ingress are independently enforced.
kubectl patch networkpolicy tenant-isolation -n tenant-a --type=json -p '[
  {"op": "add", "path": "/spec/egress/0/to/0",
   "value": {"namespaceSelector": {"matchExpressions": [{"key": "tenant", "operator": "Exists"}]}}}
]'
kubectl patch networkpolicy tenant-isolation -n tenant-c --type=json -p '[
  {"op": "add", "path": "/spec/ingress/0/from/0",
   "value": {"namespaceSelector": {"matchExpressions": [{"key": "tenant", "operator": "Exists"}]}}}
]'

TA_POD=$(kubectl -n tenant-a get pod -l app=notes-api -o jsonpath='{.items[0].metadata.name}')
TC_POD_IP=$(kubectl -n tenant-c get pod -l app=notes-api -o jsonpath='{.items[0].status.podIP}')
echo ">>> Issuing cross-tenant requests directly to tenant-c's pod IP: tenant-a ($TA_POD) -> $TC_POD_IP"
for i in 1 2 3; do
  kubectl -n tenant-a exec "$TA_POD" -- python3 -c "
import urllib.request
urllib.request.urlopen('http://$TC_POD_IP:8000/healthz', timeout=3)
print('cross-tenant request succeeded')
" || echo "request $i did not succeed (see output above)"
  sleep 1
done
