#!/usr/bin/env bash
set -euo pipefail

echo ">>> Removing the CPU-hog pod"
kubectl delete pod cpu-hog -n tenant-a --ignore-not-found --force --grace-period=0
