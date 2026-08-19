"""Small subprocess-based Kubernetes helpers shared by all monitors.

Deliberately not using the `kubernetes` Python client library: every monitor in this project
already shells out to `kubectl`/`docker` for the handful of one-off queries it needs (see
control-plane-monitor's docker-exec chmod trick), so adding a client-library dependency just
for `get namespaces -o json` and `get --raw ...` would be inconsistent with the rest of the
codebase for no real benefit at this scale.
"""
from __future__ import annotations

import json
import subprocess
import sys


def tenant_namespaces() -> set[str]:
    """Namespaces labeled `tenant` (infra/namespaces/*/namespace.yaml) — the set of namespaces
    that count as tenant boundaries for cross-namespace isolation checks. Without this, a
    monitor would also flag legitimate cross-namespace system traffic (e.g. anything touching
    kube-system)."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "namespaces", "-l", "tenant", "-o", "json"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        data = json.loads(result.stdout)
        return {ns["metadata"]["name"] for ns in data["items"]}
    except Exception as exc:  # kubectl not reachable yet, etc. — caller retries later
        print(f"[common.k8s] warning: could not list tenant namespaces: {exc}", file=sys.stderr)
        return set()


def kubectl_get_raw(path: str, timeout: int = 10) -> dict | None:
    """`kubectl get --raw <path>`, JSON-decoded. Used to reach kubelet's read-only stats/metrics
    endpoints through the apiserver's node proxy, so the resource-monitor can reuse the same
    kubeconfig/auth as every other kubectl call in this project instead of talking to kubelet
    directly (which needs its own TLS client cert)."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "--raw", path],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        return json.loads(result.stdout)
    except Exception as exc:
        print(f"[common.k8s] warning: kubectl get --raw {path} failed: {exc}", file=sys.stderr)
        return None


def kubectl_get_raw_text(path: str, timeout: int = 10) -> str | None:
    """Same as kubectl_get_raw but for non-JSON endpoints (Prometheus text exposition format,
    e.g. /metrics/cadvisor)."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "--raw", path],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        return result.stdout
    except Exception as exc:
        print(f"[common.k8s] warning: kubectl get --raw {path} failed: {exc}", file=sys.stderr)
        return None


def node_names() -> list[str]:
    try:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        data = json.loads(result.stdout)
        return [n["metadata"]["name"] for n in data["items"]]
    except Exception as exc:
        print(f"[common.k8s] warning: could not list nodes: {exc}", file=sys.stderr)
        return []
