#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

echo ">>> Removing the CPU-hog pods"
remove_hogs
