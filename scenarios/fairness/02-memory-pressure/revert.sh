#!/usr/bin/env bash
set -euo pipefail

echo ">>> Removing the memory-hog pod"
kubectl delete pod memory-hog -n tenant-a --ignore-not-found --force --grace-period=0
