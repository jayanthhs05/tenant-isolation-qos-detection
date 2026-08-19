#!/usr/bin/env python3
"""Fusion layer CLI (Phase 8).

Batch-reads the JSONL event logs written by control-plane-monitor, network-flow-monitor, and
resource-monitor, correlates and classifies them (see classify.py), and writes the resulting
Alerts to a JSONL file. Batch rather than live-tailing: this is the shape the evaluation
harness (Phase 11) needs — run a scenario to completion, then score the resulting logs against
ground truth — and it can be re-run against a growing log directory at any point during a live
session too.

Usage:
    python3 run_fusion.py [--control-plane-events PATH] [--network-flow-events PATH]
                           [--resource-events PATH] [--output PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # fusion/, for `src`
from src import Alert, classify_events  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "monitors"))  # for `common`
from common import Event  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CP_EVENTS = ROOT_DIR / "monitors" / "control-plane-monitor" / "events.jsonl"
DEFAULT_NF_EVENTS = ROOT_DIR / "monitors" / "network-flow-monitor" / "events.jsonl"
DEFAULT_RES_EVENTS = ROOT_DIR / "monitors" / "resource-monitor" / "events.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "alerts.jsonl"


def load_events(path: Path) -> list[Event]:
    if not path.exists():
        print(f"[fusion] note: {path} does not exist yet, skipping", file=sys.stderr)
        return []
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(Event.from_json(line))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-plane-events", default=str(DEFAULT_CP_EVENTS))
    parser.add_argument("--network-flow-events", default=str(DEFAULT_NF_EVENTS))
    parser.add_argument("--resource-events", default=str(DEFAULT_RES_EVENTS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    events: list[Event] = []
    for path_str in (args.control_plane_events, args.network_flow_events, args.resource_events):
        events.extend(load_events(Path(path_str)))

    print(f"[fusion] loaded {len(events)} events")
    alerts = classify_events(events)
    print(f"[fusion] emitting {len(alerts)} alerts")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        for alert in alerts:
            fh.write(alert.to_json() + "\n")
            print(f"[ALERT] {alert.category}: {alert.tenants[0]} <-> {alert.tenants[1]} "
                  f"(sources={alert.sources}, latency={alert.detection_latency_seconds:.2f}s)")

    print(f"[fusion] wrote alerts to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
