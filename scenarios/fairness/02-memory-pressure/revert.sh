#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

echo ">>> Removing the memory-pressure pods"
remove_hogs
