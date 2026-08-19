#!/usr/bin/env python3
"""Resource / fairness monitor (Phase 7).

Polls kubelet's stats/summary (per-pod CPU/memory usage) and cAdvisor's Prometheus metrics
(CPU CFS throttling counters) through the apiserver's node proxy, for every tenant namespace's
pods — the resource-plane half of the isolation/fairness split described in
docs/architecture.md. This is deliberately a *separate* collector from network-flow-monitor:
Hubble's eBPF probes see the network/socket layer only and cannot see cgroup CPU pressure,
which lives in kubelet/cAdvisor instead (docs/risk-log.md #1).

Elevated usage alone is not evidence of a fairness violation — a tenant is allowed to use the
CPU it's asking for. This monitor only reports raw pressure signals (sustained high usage
relative to the pod's own limit, or actual CFS throttling); the fusion layer (Phase 8) is
responsible for correlating that with an independent, victim-side QoS breach before
classifying anything as a fairness violation.

Usage:
    python3 monitor.py [--output PATH] [--interval-seconds N] [--cpu-pressure-ratio 0.85]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for `monitors.common`
from common import (  # noqa: E402
    Event,
    JsonlSink,
    kubectl_get_raw,
    kubectl_get_raw_text,
    node_names,
    now_iso,
    parse_cpu_quantity,
    parse_metric,
    tenant_namespaces,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "events.jsonl"
THROTTLE_METRIC = "container_cpu_cfs_throttled_periods_total"


def pod_cpu_limits(tenants: set[str]) -> dict[tuple[str, str], float | None]:
    """(namespace, pod_name) -> first container's CPU limit in cores (None if unset). Only
    the first container matters here: every tenant Deployment in this project runs exactly one
    container (app/k8s/app.yaml.tmpl)."""
    data = kubectl_get_raw("/api/v1/pods") or {}
    limits: dict[tuple[str, str], float | None] = {}
    for pod in data.get("items", []):
        namespace = pod.get("metadata", {}).get("namespace")
        if namespace not in tenants:
            continue
        name = pod.get("metadata", {}).get("name")
        containers = pod.get("spec", {}).get("containers", [])
        cpu_limit = None
        if containers:
            cpu_limit = parse_cpu_quantity(containers[0].get("resources", {}).get("limits", {}).get("cpu"))
        limits[(namespace, name)] = cpu_limit
    return limits


def poll_usage(node: str, tenants: set[str]) -> list[dict]:
    """Per-pod CPU/memory usage for this node's tenant pods, from kubelet's stats/summary."""
    data = kubectl_get_raw(f"/api/v1/nodes/{node}/proxy/stats/summary")
    if data is None:
        return []
    out = []
    for pod in data.get("pods", []):
        ref = pod.get("podRef", {})
        namespace, name = ref.get("namespace"), ref.get("name")
        if namespace not in tenants:
            continue
        cpu = pod.get("cpu", {}).get("usageNanoCores")
        memory = pod.get("memory", {}).get("workingSetBytes")
        out.append({"namespace": namespace, "pod": name, "node": node,
                     "usageNanoCores": cpu, "workingSetBytes": memory})
    return out


def poll_throttling(node: str, tenants: set[str]) -> dict[tuple[str, str, str], float]:
    """(namespace, pod, container) -> cumulative CFS-throttled-periods counter, for this
    node's tenant containers. Excludes the empty-container pod-level cgroup rollup line
    (container="") — only real containers are meaningful here."""
    text = kubectl_get_raw_text(f"/api/v1/nodes/{node}/proxy/metrics/cadvisor")
    if text is None:
        return {}
    out = {}
    for labels, value in parse_metric(text, THROTTLE_METRIC):
        namespace, pod, container = labels.get("namespace"), labels.get("pod"), labels.get("container")
        if namespace not in tenants or not container:
            continue
        out[(namespace, pod, container)] = value
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--cpu-pressure-ratio", type=float, default=0.85,
                         help="usageNanoCores / container CPU limit above which usage itself is reported")
    parser.add_argument("--tenants-refresh-seconds", type=int, default=30)
    args = parser.parse_args()

    print(f"[resource-monitor] writing alerts to {args.output}")

    tenants = tenant_namespaces()
    nodes = node_names()
    limits = pod_cpu_limits(tenants)
    print(f"[resource-monitor] tenant namespaces: {sorted(tenants) or '(none yet)'}, nodes: {nodes}")
    last_refresh = time.monotonic()

    # cumulative counters — need a previous poll's value to compute a per-interval delta
    prev_throttle: dict[tuple[str, str, str], float] = {}

    with JsonlSink(args.output) as sink:
        while True:
            if time.monotonic() - last_refresh > args.tenants_refresh_seconds:
                tenants = tenant_namespaces() or tenants
                nodes = node_names() or nodes
                limits = pod_cpu_limits(tenants)
                last_refresh = time.monotonic()

            for node in nodes:
                for pod in poll_usage(node, tenants):
                    cpu_cores = pod["usageNanoCores"] / 1e9 if pod["usageNanoCores"] is not None else None
                    limit = limits.get((pod["namespace"], pod["pod"]))
                    if cpu_cores is None or not limit:
                        continue
                    ratio = cpu_cores / limit
                    if ratio >= args.cpu_pressure_ratio:
                        event = Event(
                            source="resource",
                            timestamp=now_iso(),
                            namespace=pod["namespace"],
                            principal=pod["pod"],
                            peer_namespace=None,
                            resource_type="pod-cpu-usage",
                            verb="pressure",
                            raw={**pod, "cpu_limit_cores": limit, "usage_ratio": ratio},
                        )
                        sink.write(event)
                        print(f"[ALERT] resource pressure: {pod['namespace']}/{pod['pod']} "
                              f"at {ratio:.0%} of its {limit:.2f}-core CPU limit")

                throttle_now = poll_throttling(node, tenants)
                for key, value in throttle_now.items():
                    namespace, podname, container = key
                    delta = value - prev_throttle.get(key, value)
                    if delta > 0:
                        event = Event(
                            source="resource",
                            timestamp=now_iso(),
                            namespace=namespace,
                            principal=podname,
                            peer_namespace=None,
                            resource_type="pod-cpu-throttle",
                            verb="throttled",
                            raw={"namespace": namespace, "pod": podname, "container": container,
                                 "node": node, "throttled_periods_delta": delta,
                                 "throttled_periods_total": value},
                        )
                        sink.write(event)
                        print(f"[ALERT] CPU throttled: {namespace}/{podname} ({container}) "
                              f"+{delta:.0f} throttled periods on {node}")
                prev_throttle.update(throttle_now)

            time.sleep(args.interval_seconds)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[resource-monitor] shutting down...", file=sys.stderr)
