#!/usr/bin/env bash
# Shared helpers for the fairness scenarios. Sourced by each inject.sh.
#
# A noisy neighbor only degrades a victim if they genuinely contend for the same CPU. Hogs are
# therefore pinned (nodeName) to the node running the victim tenant's pod, and
# scripts/constrain-node.sh limits that node's CPU so a handful of CFS-throttled hogs can
# actually saturate it (see docs/risk-log.md #15).

VICTIM_TENANT="${VICTIM_TENANT:-tenant-b}"
HOG_LABEL="app=noisy-hog"

victim_node() {
  kubectl get pod -n "${VICTIM_TENANT}" -l app=notes-api -o jsonpath='{.items[0].spec.nodeName}'
}

# launch_hogs NAME_PREFIX COUNT CPU_REQ CPU_LIM MEM_REQ MEM_LIM PYTHON_CODE
launch_hogs() {
  local prefix="$1" count="$2" cpu_req="$3" cpu_lim="$4" mem_req="$5" mem_lim="$6" code="$7"
  local node; node="$(victim_node)"
  echo ">>> Launching ${count} hog pod(s) in tenant-a on ${node} (colocated with ${VICTIM_TENANT})"
  for i in $(seq 1 "${count}"); do
    kubectl run "${prefix}-${i}" -n tenant-a --image=python:3.12-slim-bookworm --restart=Never \
      --labels="${HOG_LABEL}" --overrides="$(python3 - "$node" "$cpu_req" "$cpu_lim" "$mem_req" "$mem_lim" "$code" <<'PY'
import json, sys
node, cpu_req, cpu_lim, mem_req, mem_lim, code = sys.argv[1:]
print(json.dumps({"spec": {"nodeName": node, "serviceAccountName": "tenant-a-app", "containers": [{
    "name": "hog", "image": "python:3.12-slim-bookworm", "command": ["python3", "-c", code],
    "resources": {"requests": {"cpu": cpu_req, "memory": mem_req}, "limits": {"cpu": cpu_lim, "memory": mem_lim}}}]}}))
PY
)" >/dev/null
  done
}

remove_hogs() {
  kubectl delete pod -n tenant-a -l "${HOG_LABEL}" --ignore-not-found --force --grace-period=0
}
