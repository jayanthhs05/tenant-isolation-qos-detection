#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

# Six single-threaded busy-loop batch pods, each CFS-limited to 200m: individually well-behaved
# against their quota, but together (1.2 cores) they saturate the CPU-constrained victim node.
echo ">>> Launching CPU-hog batch pods in tenant-a (noisy-neighbor batch workload)"
launch_hogs cpu-hog "${HOG_COUNT:-6}" 100m 200m 64Mi 128Mi "while True: pass"
