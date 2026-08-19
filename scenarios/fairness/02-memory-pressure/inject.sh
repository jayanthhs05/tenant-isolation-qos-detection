#!/usr/bin/env bash
set -euo pipefail

echo ">>> Launching a memory-hog pod in tenant-a (simulates a noisy-neighbor batch job)"
kubectl run memory-hog -n tenant-a --image=python:3.12-slim-bookworm --restart=Never \
  --overrides='{
    "spec": {
      "serviceAccountName": "tenant-a-app",
      "containers": [{
        "name": "memory-hog",
        "image": "python:3.12-slim-bookworm",
        "command": ["python3", "-c",
          "import time\nchunks = []\nfor _ in range(40):\n    chunks.append(bytearray(5 * 1024 * 1024))\n    time.sleep(0.5)\ntime.sleep(30)"],
        "resources": {
          "requests": {"cpu": "50m", "memory": "64Mi"},
          "limits": {"cpu": "200m", "memory": "180Mi"}
        }
      }]
    }
  }'
