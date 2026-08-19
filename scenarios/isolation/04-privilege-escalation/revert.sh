#!/usr/bin/env bash
set -euo pipefail

echo ">>> Removing the cluster-admin escalation"
kubectl delete clusterrolebinding tenant-a-privesc --ignore-not-found
