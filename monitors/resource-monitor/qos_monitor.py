#!/usr/bin/env python3
"""Victim-side QoS breach detector (Phase 11).

The k6-fed half of resource-monitor's output — per docs/architecture.md's diagram, the k6
load generator feeds into resource-monitor's own event stream rather than being a separate
top-level source, so everything this script emits still carries `source: "resource"` and
lands in the same events.jsonl file resource-monitor's cgroup-pressure events do. Kept as its
own script (not folded into monitor.py) because it tails a completely different input — k6's
JSON output, not kubectl/cAdvisor — and has no cluster polling of its own.

Tails a k6 `--out json=PATH` file, computes a rolling p95 of http_req_duration over a sliding
window, and once an initial baseline settles, emits a `victim-qos-latency` breach Event
whenever the rolling p95 exceeds `--breach-ratio` x that baseline. This is the independent,
victim-side evidence fusion's fairness rule (fusion/src/classify.py) requires alongside a
noisy tenant's own resource pressure — elevated CPU/memory on its own is never sufficient.

Usage:
    kubectl -n tenant-b port-forward svc/notes-api 18080:80 &
    TARGET_URL=http://localhost:18080/healthz k6 run --out json=/tmp/k6-tenant-b.jsonl \
      load/k6/tenant-load-script.js &
    python3 qos_monitor.py --k6-output /tmp/k6-tenant-b.jsonl --tenant tenant-b
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for `monitors.common`
from common import Event, JsonlSink, now_iso  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "events.jsonl"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lower, upper = int(k), min(int(k) + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (k - lower)


def follow(path: Path):
    """Same tail-with-tolerance shape as control-plane-monitor.follow(), simplified: k6's
    output file is host-local (no permission/rotation quirks to work around here). Yields
    `None` on every idle wait rather than just looping silently — main()'s baseline-deadline
    check needs to run on a wall-clock cadence even during a gap in k6 output, not only when a
    new line happens to arrive."""
    fh = None
    while True:
        if fh is None:
            try:
                fh = open(path, "r")
            except FileNotFoundError:
                time.sleep(1)
                yield None
                continue
        line = fh.readline()
        if line:
            yield line
        else:
            time.sleep(0.2)
            yield None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k6-output", required=True, help="path passed to k6's --out json=")
    parser.add_argument("--tenant", required=True, help="victim tenant namespace this k6 run targets")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--baseline-seconds", type=float, default=10.0,
                         help="how long to sample before freezing the baseline p95")
    parser.add_argument("--rolling-window-seconds", type=float, default=10.0)
    parser.add_argument("--breach-ratio", type=float, default=1.5)
    parser.add_argument("--check-interval-seconds", type=float, default=2.0)
    parser.add_argument("--reemit-seconds", type=float, default=5.0,
                         help="while a breach is sustained, re-emit an event this often so fusion's "
                              "time-window correlation sees the whole breach, not just its onset")
    args = parser.parse_args()

    print(f"[qos-monitor] tailing {args.k6_output} for tenant={args.tenant}")
    print(f"[qos-monitor] writing breach events to {args.output}")

    baseline_samples: list[float] = []
    baseline_p95: float | None = None
    baseline_deadline = time.monotonic() + args.baseline_seconds
    rolling: deque[tuple[float, float]] = deque()  # (monotonic_ts, value_ms)
    last_check = time.monotonic()
    breached = False
    last_emit = 0.0  # monotonic time of the last emitted breach event

    with JsonlSink(args.output) as sink:
        for line in follow(Path(args.k6_output)):
            # Runs on every heartbeat (line is None) as well as every real line, so the
            # baseline finalizes on a wall-clock cadence even if k6 output pauses right at the
            # baseline/rolling boundary — see follow()'s docstring.
            if baseline_p95 is None and baseline_samples and time.monotonic() >= baseline_deadline:
                baseline_p95 = percentile(baseline_samples, 0.95)
                print(f"[qos-monitor] baseline p95 established: {baseline_p95:.1f}ms "
                      f"({len(baseline_samples)} samples)")

            if line is None:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("metric") != "http_req_duration" or record.get("type") != "Point":
                continue
            value_ms = record.get("data", {}).get("value")
            if value_ms is None:
                continue
            now_mono = time.monotonic()

            if baseline_p95 is None:
                baseline_samples.append(value_ms)
                continue

            rolling.append((now_mono, value_ms))
            while rolling and now_mono - rolling[0][0] > args.rolling_window_seconds:
                rolling.popleft()

            if now_mono - last_check < args.check_interval_seconds:
                continue
            last_check = now_mono

            rolling_p95 = percentile([v for _, v in rolling], 0.95)
            ratio = rolling_p95 / baseline_p95 if baseline_p95 else 0.0
            is_breach = ratio >= args.breach_ratio

            if is_breach and (not breached or now_mono - last_emit >= args.reemit_seconds):
                last_emit = now_mono
                event = Event(
                    source="resource",
                    timestamp=now_iso(),
                    namespace=args.tenant,
                    principal=None,
                    peer_namespace=None,
                    resource_type="victim-qos-latency",
                    verb="breach",
                    raw={"tenant": args.tenant, "rolling_p95_ms": rolling_p95,
                         "baseline_p95_ms": baseline_p95, "ratio": ratio,
                         "window_seconds": args.rolling_window_seconds},
                )
                sink.write(event)
                print(f"[ALERT] QoS breach: {args.tenant} rolling p95={rolling_p95:.1f}ms "
                      f"({ratio:.2f}x baseline {baseline_p95:.1f}ms)")
            breached = is_breach

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[qos-monitor] shutting down...", file=sys.stderr)
