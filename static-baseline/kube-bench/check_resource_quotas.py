#!/usr/bin/env python3
"""Supplementary static check: per-tenant ResourceQuota/LimitRange presence.

kube-bench does not check for missing or misconfigured ResourceQuotas at all (see
docs/risk-log.md #2) — its CIS checks are about apiserver/kubelet/etcd configuration and pod
security settings, not per-namespace resource governance. Since the proposal's static baseline
claims to cover "basic resource-quota misconfiguration" alongside CIS security checks, this
script closes that gap: for every tenant namespace (identified by the `tenant` label set in
infra/namespaces/*/namespace.yaml), it checks that both a ResourceQuota (with cpu/memory limits
set) and a LimitRange exist. This is deliberately a static, one-time check — the runtime
fairness/QoS track (monitors/resource-monitor, Phase 7) is what actually watches quota
*enforcement* in real time.

Usage: python3 check_resource_quotas.py [--json OUT_FILE]
Exit code: 0 if every tenant namespace passes both checks, 1 otherwise.
"""
import argparse
import json
import subprocess
import sys


def kubectl_json(*args: str) -> dict:
    result = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def find_tenant_namespaces() -> list[str]:
    namespaces = kubectl_json("get", "namespaces", "-l", "tenant")
    return [ns["metadata"]["name"] for ns in namespaces["items"]]


def check_namespace(namespace: str) -> dict:
    quotas = kubectl_json("get", "resourcequota", "-n", namespace)["items"]
    limit_ranges = kubectl_json("get", "limitrange", "-n", namespace)["items"]

    has_cpu_mem_quota = any(
        "limits.cpu" in q["spec"].get("hard", {}) and "limits.memory" in q["spec"].get("hard", {})
        for q in quotas
    )
    has_limit_range = len(limit_ranges) > 0

    return {
        "namespace": namespace,
        "has_resourcequota_with_cpu_and_memory_limits": has_cpu_mem_quota,
        "has_limitrange": has_limit_range,
        "pass": has_cpu_mem_quota and has_limit_range,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write structured results to this file")
    args = parser.parse_args()

    namespaces = find_tenant_namespaces()
    if not namespaces:
        print("No tenant namespaces found (label `tenant` not set on any namespace).", file=sys.stderr)
        return 1

    results = [check_namespace(ns) for ns in namespaces]

    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"[{status}] {r['namespace']}: "
              f"ResourceQuota(cpu+mem limits)={r['has_resourcequota_with_cpu_and_memory_limits']} "
              f"LimitRange={r['has_limitrange']}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"checks": results}, f, indent=2)
        print(f"\nWrote {args.json}")

    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
