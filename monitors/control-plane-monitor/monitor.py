#!/usr/bin/env python3
"""Control-plane monitor (Phase 5).

Tails the Kubernetes audit log and flags any principal from one tenant's namespace accessing
a resource scoped to a different tenant's namespace — the isolation track's control-plane
signal (docs/architecture.md). Runs as a plain host process during development (not yet
deployed in-cluster); the audit log is read directly off the host bind-mount created by
infra/kind/kind-config.yaml.tmpl.

Usage:
    python3 monitor.py [--audit-log PATH] [--output PATH] [--tenants-refresh-seconds N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for `monitors.common`
from common import Event, JsonlSink, now_iso  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_LOG = ROOT_DIR / "infra" / "kind" / "audit" / "audit.log"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "events.jsonl"

SERVICEACCOUNT_RE = re.compile(r"^system:serviceaccount:(?P<namespace>[^:]+):(?P<name>.+)$")
NODE_AUDIT_LOG_PATH = "/etc/kubernetes/audit/audit.log"


def tenant_namespaces() -> set[str]:
    """Namespaces labeled `tenant` (infra/namespaces/*/namespace.yaml) — only cross-namespace
    access *between two of these* counts as a candidate isolation violation. Without this, the
    monitor would also flag entirely legitimate cross-namespace system traffic (e.g. anything
    touching kube-system)."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "namespaces", "-l", "tenant", "-o", "json"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        data = json.loads(result.stdout)
        return {ns["metadata"]["name"] for ns in data["items"]}
    except Exception as exc:  # kubectl not reachable yet, etc. — caller retries later
        print(f"[control-plane-monitor] warning: could not list tenant namespaces: {exc}", file=sys.stderr)
        return set()


def _try_fix_permissions(container: str) -> None:
    """audit.log is root:root 600 on the host (root inside the apiserver container writes it);
    this restores 644 the same way scripts/bootstrap-cluster.sh does after cluster creation.
    Needed again if kube-apiserver's own log rotation creates a fresh root-owned file mid-run —
    see docs/risk-log.md #9."""
    try:
        subprocess.run(
            ["docker", "exec", container, "chmod", "644", NODE_AUDIT_LOG_PATH],
            capture_output=True, timeout=10, check=False,
        )
    except Exception as exc:
        print(f"[control-plane-monitor] warning: could not chmod audit.log via docker exec: {exc}", file=sys.stderr)


def follow(path: Path, container: str):
    """Yields new lines appended to `path`, tolerating the file not existing yet, permission
    errors (auto-fixed via docker exec, see _try_fix_permissions), and rotation/truncation
    (detected via inode change, reopened from the start of the new file)."""
    fh = None
    inode = None
    while True:
        if fh is None:
            try:
                fh = open(path, "r")
                inode = os.fstat(fh.fileno()).st_ino
            except FileNotFoundError:
                time.sleep(1)
                continue
            except PermissionError:
                _try_fix_permissions(container)
                time.sleep(1)
                continue

        line = fh.readline()
        if line:
            yield line
            continue

        # No new data — check whether the file was rotated out from under us before sleeping.
        try:
            current_inode = os.stat(path).st_ino
        except FileNotFoundError:
            current_inode = None
        if current_inode != inode:
            fh.close()
            fh = None
        time.sleep(0.5)


def parse_audit_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def classify(record: dict, tenants: set[str]) -> Event | None:
    if record.get("stage") != "ResponseComplete":
        return None

    username = record.get("user", {}).get("username", "")
    match = SERVICEACCOUNT_RE.match(username)
    if not match:
        return None  # not a tenant app's own ServiceAccount (human user, system:node, etc.)
    principal_namespace = match.group("namespace")

    object_ref = record.get("objectRef") or {}
    resource_namespace = object_ref.get("namespace")

    if (
        principal_namespace in tenants
        and resource_namespace in tenants
        and resource_namespace != principal_namespace
    ):
        return Event(
            source="control-plane",
            timestamp=record.get("stageTimestamp") or now_iso(),
            namespace=principal_namespace,
            principal=username,
            peer_namespace=resource_namespace,
            resource_type=object_ref.get("resource"),
            verb=record.get("verb"),
            raw=record,
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-log", default=str(DEFAULT_AUDIT_LOG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--container", default="tenant-lab-control-plane",
                         help="kind node container to docker-exec chmod fixes against")
    parser.add_argument("--tenants-refresh-seconds", type=int, default=30)
    args = parser.parse_args()

    print(f"[control-plane-monitor] tailing {args.audit_log}")
    print(f"[control-plane-monitor] writing alerts to {args.output}")

    tenants = tenant_namespaces()
    print(f"[control-plane-monitor] tenant namespaces: {sorted(tenants) or '(none yet)'}")
    last_refresh = time.monotonic()

    with JsonlSink(args.output) as sink:
        for line in follow(Path(args.audit_log), args.container):
            if time.monotonic() - last_refresh > args.tenants_refresh_seconds:
                tenants = tenant_namespaces() or tenants
                last_refresh = time.monotonic()

            record = parse_audit_line(line)
            if record is None:
                continue

            event = classify(record, tenants)
            if event is not None:
                sink.write(event)
                print(
                    f"[ALERT] isolation candidate: {event.principal} "
                    f"({event.namespace} -> {event.peer_namespace}) "
                    f"{event.verb} {event.resource_type} at {event.timestamp}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
