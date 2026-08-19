#!/usr/bin/env python3
"""Network-flow monitor (Phase 6).

Wraps `hubble observe -f -o json` and flags flows that cross a tenant namespace boundary —
the isolation track's network/socket-layer signal (docs/architecture.md). Complements
control-plane-monitor: a NetworkPolicy misconfiguration that lets cross-tenant traffic through
never touches the apiserver audit log at all, so it's invisible to that monitor but fully
visible here via Hubble's eBPF-derived flow verdicts (FORWARDED vs DROPPED).

Manages its own `kubectl port-forward` to the hubble-relay Service so this can be run
standalone (`python3 monitor.py`) without any manual port-forward setup first.

Usage:
    python3 monitor.py [--output PATH] [--relay-service svc/hubble-relay]
                        [--relay-namespace kube-system] [--local-port 4245]
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for `monitors.common`
from common import Event, JsonlSink, now_iso, tenant_namespaces  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "events.jsonl"

# Cilium bakes the pod's ServiceAccount into its identity labels, e.g.
# "k8s:io.cilium.k8s.policy.serviceaccount=tenant-a-app" — cheaper and just as reliable as
# running a separate Kubernetes-client Pod watch purely to look up SA-per-pod (see
# docs/architecture.md's note on enrichment, updated to reflect this simpler approach).
SERVICEACCOUNT_LABEL_RE = re.compile(r"^k8s:io\.cilium\.k8s\.policy\.serviceaccount=(?P<name>.+)$")


def _extract_principal(endpoint: dict, namespace: str | None) -> str | None:
    for label in endpoint.get("labels") or []:
        match = SERVICEACCOUNT_LABEL_RE.match(label)
        if match:
            sa = match.group("name")
            return f"system:serviceaccount:{namespace}:{sa}" if namespace else sa
    pod_name = endpoint.get("pod_name")
    return f"{namespace}/{pod_name}" if namespace and pod_name else pod_name


def classify(record: dict, tenants: set[str]) -> Event | None:
    flow = record.get("flow") or record  # top-level is usually {"flow": {...}}, be lenient
    source = flow.get("source") or {}
    destination = flow.get("destination") or {}

    source_ns = source.get("namespace")
    dest_ns = destination.get("namespace")
    verdict = flow.get("verdict")

    if source_ns in tenants and dest_ns in tenants and source_ns != dest_ns:
        return Event(
            source="network-flow",
            timestamp=flow.get("time") or now_iso(),
            namespace=source_ns,
            principal=_extract_principal(source, source_ns),
            peer_namespace=dest_ns,
            resource_type="network-flow",
            verb=verdict,  # FORWARDED | DROPPED | ERROR | AUDIT
            raw=record,
        )

    # On an egress-side policy deny, Cilium drops the packet before it ever reaches the peer's
    # endpoint, so the peer's identity — and therefore its namespace — is never resolved
    # (confirmed live against this cluster: `destination.namespace` is absent, only
    # `destination.identity` for the fallback "world" identity is present). That still means a
    # tenant's own pod tried to cross its namespace's egress boundary and got blocked — the
    # same "flag attempted access regardless of allow/deny outcome" reasoning
    # control-plane-monitor applies to RBAC denials — so surface it with peer_namespace
    # unresolved rather than silently dropping the signal.
    if verdict == "DROPPED" and flow.get("drop_reason_desc") == "POLICY_DENIED" and source_ns in tenants and not dest_ns:
        return Event(
            source="network-flow",
            timestamp=flow.get("time") or now_iso(),
            namespace=source_ns,
            principal=_extract_principal(source, source_ns),
            peer_namespace=None,
            resource_type="network-flow-egress-denied",
            verb=verdict,
            raw=record,
        )

    return None


def _port_forward(relay_service: str, relay_namespace: str, local_port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["kubectl", "-n", relay_namespace, "port-forward", relay_service, f"{local_port}:80"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    # Block until kubectl prints "Forwarding from ..." (i.e. the tunnel is actually up) rather
    # than a fixed sleep, with a bounded timeout — a fixed sleep either races a slow apiserver
    # or wastes time on a fast one.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"kubectl port-forward exited early (code {proc.returncode})")
        line = proc.stdout.readline()
        if "Forwarding from" in line:
            return proc
    raise RuntimeError("kubectl port-forward did not become ready within 15s")


def observe_lines(relay_service: str, relay_namespace: str, local_port: int):
    """Yields raw JSON lines from `hubble observe`, restarting both the port-forward and the
    hubble process on any failure. Justified by direct experience in this project: a
    `kubectl port-forward` to hubble-relay was observed to drop mid-session during manual
    testing (see docs/risk-log.md), so a long-running monitor must not assume either
    subprocess stays alive for the whole run."""
    while True:
        pf_proc = None
        hubble_proc = None
        try:
            pf_proc = _port_forward(relay_service, relay_namespace, local_port)
            hubble_proc = subprocess.Popen(
                ["hubble", "--server", f"127.0.0.1:{local_port}", "observe", "-f", "-o", "json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            )
            print(f"[network-flow-monitor] streaming via hubble-relay on 127.0.0.1:{local_port}")
            for line in hubble_proc.stdout:
                yield line
            # stdout closed — hubble exited, fall through to retry
            stderr = hubble_proc.stderr.read() if hubble_proc.stderr else ""
            print(f"[network-flow-monitor] hubble observe ended unexpectedly: {stderr.strip()}", file=sys.stderr)
        except Exception as exc:
            print(f"[network-flow-monitor] observe pipeline error: {exc}", file=sys.stderr)
        finally:
            for proc in (hubble_proc, pf_proc):
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
        print("[network-flow-monitor] retrying observe pipeline in 3s...", file=sys.stderr)
        time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--relay-service", default="svc/hubble-relay")
    parser.add_argument("--relay-namespace", default="kube-system")
    parser.add_argument("--local-port", type=int, default=4245)
    parser.add_argument("--tenants-refresh-seconds", type=int, default=30)
    args = parser.parse_args()

    print(f"[network-flow-monitor] writing alerts to {args.output}")

    tenants = tenant_namespaces()
    print(f"[network-flow-monitor] tenant namespaces: {sorted(tenants) or '(none yet)'}")
    last_refresh = time.monotonic()

    # SIGTERM doesn't unwind Python's call stack by default (unlike SIGINT/KeyboardInterrupt),
    # so without this, killing the monitor leaves its kubectl port-forward and hubble observe
    # child processes orphaned and running. Route it through the same exception path as
    # Ctrl+C so the generator's finally block (observe_lines) always gets a chance to run.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    lines = observe_lines(args.relay_service, args.relay_namespace, args.local_port)
    try:
        with JsonlSink(args.output) as sink:
            for line in lines:
                if time.monotonic() - last_refresh > args.tenants_refresh_seconds:
                    tenants = tenant_namespaces() or tenants
                    last_refresh = time.monotonic()

                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event = classify(record, tenants)
                if event is not None:
                    sink.write(event)
                    print(
                        f"[ALERT] isolation candidate: {event.principal} "
                        f"({event.namespace} -> {event.peer_namespace}) "
                        f"flow {event.verb} at {event.timestamp}"
                    )
    except KeyboardInterrupt:
        print("[network-flow-monitor] shutting down...", file=sys.stderr)
    finally:
        lines.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
