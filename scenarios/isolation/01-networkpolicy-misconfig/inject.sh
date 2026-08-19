#!/usr/bin/env bash
set -euo pipefail
: "${ROOT_DIR:?ROOT_DIR must be set by the driver}"

echo ">>> Broadening tenant-a's NetworkPolicy egress AND tenant-b's ingress to any tenant namespace"
# Both sides must be broadened: NetworkPolicy requires the source's egress AND the
# destination's ingress to both allow a flow, so widening only one side (as this scenario
# originally did) still leaves the packet blocked on the other leg — confirmed live: a
# request timed out with only tenant-b's ingress broadened, per docs/risk-log.md #13's
# egress-drop behavior. A real operator broadening both while chasing a "connection refused"
# and forgetting to narrow back is exactly this failure mode.
kubectl patch networkpolicy tenant-isolation -n tenant-a --type=json -p '[
  {"op": "add", "path": "/spec/egress/0/to/0",
   "value": {"namespaceSelector": {"matchExpressions": [{"key": "tenant", "operator": "Exists"}]}}}
]'
kubectl patch networkpolicy tenant-isolation -n tenant-b --type=json -p '[
  {"op": "add", "path": "/spec/ingress/0/from/0",
   "value": {"namespaceSelector": {"matchExpressions": [{"key": "tenant", "operator": "Exists"}]}}}
]'

TA_POD=$(kubectl -n tenant-a get pod -l app=notes-api -o jsonpath='{.items[0].metadata.name}')
# Deliberately the peer pod's own IP, not its Service ClusterIP: this cluster runs Cilium with
# kubeProxyReplacement disabled (infra/cilium/values.yaml), so kube-proxy — not Cilium — owns
# Service NAT. Cilium's eBPF policy engine evaluates NetworkPolicy at the pod's veth *before*
# that NAT happens, against the still-unresolved ClusterIP, which matches no known tenant
# identity — so a Service-IP request is dropped regardless of how permissive the
# NetworkPolicy is. Confirmed live: broadening both sides' policies as above still left a
# ClusterIP request timing out, while the identical request to the pod's own IP succeeded
# immediately. Going straight to the pod IP is what actually exercises the NetworkPolicy path.
TB_POD_IP=$(kubectl -n tenant-b get pod -l app=notes-api -o jsonpath='{.items[0].status.podIP}')
echo ">>> Issuing cross-tenant requests: tenant-a ($TA_POD) -> tenant-b pod ($TB_POD_IP)"
for i in 1 2 3; do
  kubectl -n tenant-a exec "$TA_POD" -- python3 -c "
import urllib.request
urllib.request.urlopen('http://$TB_POD_IP:8000/healthz', timeout=3)
print('cross-tenant request succeeded')
" || echo "request $i did not succeed (see output above)"
  sleep 1
done
