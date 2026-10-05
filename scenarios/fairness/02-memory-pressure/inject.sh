#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

# Each pod ramps memory up (5MB / 0.3s) against a 140Mi limit, then keeps rewriting what it
# holds — a memory-heavy batch job that is also CPU-bound on its working set, so the pressure
# both shows up in workingSetBytes and contends with the victim for CPU.
CODE='import time
chunks = []
for _ in range(25):
    chunks.append(bytearray(5 * 1024 * 1024))
    time.sleep(0.3)
while True:
    for c in chunks:
        c[:] = bytes(len(c))'
echo ">>> Launching memory-pressure pods in tenant-a"
launch_hogs memory-hog "${HOG_COUNT:-6}" 100m 200m 64Mi 140Mi "${CODE}"
