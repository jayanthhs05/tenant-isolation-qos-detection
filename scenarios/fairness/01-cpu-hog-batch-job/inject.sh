#!/usr/bin/env bash
set -euo pipefail

echo ">>> Launching a CPU-hog pod in tenant-a (simulates a noisy-neighbor batch job)"
kubectl run cpu-hog -n tenant-a --image=python:3.12-slim-bookworm --restart=Never \
  --overrides='{
    "spec": {
      "serviceAccountName": "tenant-a-app",
      "containers": [{
        "name": "cpu-hog",
        "image": "python:3.12-slim-bookworm",
        "command": ["python3", "-c", "while True: pass"],
        "resources": {
          "requests": {"cpu": "100m", "memory": "64Mi"},
          "limits": {"cpu": "200m", "memory": "128Mi"}
        }
      }]
    }
  }'
