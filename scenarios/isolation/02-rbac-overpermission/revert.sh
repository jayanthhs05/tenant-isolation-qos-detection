#!/usr/bin/env bash
set -euo pipefail

echo ">>> Removing the over-permissioned RoleBinding"
kubectl delete rolebinding tenant-a-overpermission -n tenant-b --ignore-not-found
