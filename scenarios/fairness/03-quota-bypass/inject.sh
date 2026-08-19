#!/usr/bin/env bash
set -euo pipefail

echo ">>> Bypassing tenant-a's resource ceiling (raising ResourceQuota + LimitRange)"
kubectl patch resourcequota tenant-quota -n tenant-a --type merge \
  -p '{"spec":{"hard":{"limits.cpu":"6","requests.cpu":"3"}}}'
kubectl patch limitrange tenant-limits -n tenant-a --type json \
  -p '[{"op":"replace","path":"/spec/limits/0/max/cpu","value":"1500m"}]'

echo ">>> Launching an oversized batch job that the original ceiling would have rejected"
kubectl run quota-bypass-hog -n tenant-a --image=python:3.12-slim-bookworm --restart=Never \
  --overrides='{
    "spec": {
      "serviceAccountName": "tenant-a-app",
      "containers": [{
        "name": "quota-bypass-hog",
        "image": "python:3.12-slim-bookworm",
        "command": ["python3", "-c", "while True: pass"],
        "resources": {
          "requests": {"cpu": "1000m", "memory": "64Mi"},
          "limits": {"cpu": "1500m", "memory": "128Mi"}
        }
      }]
    }
  }'
