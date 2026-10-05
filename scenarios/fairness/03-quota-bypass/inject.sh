#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

echo ">>> Bypassing tenant-a's resource ceiling (raising ResourceQuota + LimitRange)"
kubectl patch resourcequota tenant-quota -n tenant-a --type merge \
  -p '{"spec":{"hard":{"limits.cpu":"6","requests.cpu":"3"}}}'
kubectl patch limitrange tenant-limits -n tenant-a --type json \
  -p '[{"op":"replace","path":"/spec/limits/0/max/cpu","value":"1500m"}]'

echo ">>> Launching oversized batch pods that the original ceiling would have rejected"
# Two busy processes in one pod: a single-threaded loop can never exceed 1 core, so it would
# never approach the 1500m ceiling this scenario is about.
CODE='import multiprocessing as mp
def spin():
    while True:
        pass
for _ in range(2):
    mp.Process(target=spin).start()
spin()'
launch_hogs quota-bypass-hog "${HOG_COUNT:-1}" 1000m 1200m 64Mi 128Mi "${CODE}"
