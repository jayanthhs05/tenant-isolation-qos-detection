#!/usr/bin/env python3
"""Post-hoc sensitivity analysis of qos_monitor.py's --breach-ratio.

Replays the k6 output of a finished sweep through the same rolling-p95 logic qos_monitor.py runs
live (baseline from the first --baseline-seconds, rolling window, check cadence, re-emit
cadence), but in k6's own event time, for several breach ratios. For each ratio, the replayed
victim-qos-latency events replace the live ones and the fairness configs are re-scored with
compute_metrics.score_config. Nothing here touches the cluster.

This is a POST-HOC analysis on the same data the thresholds are then evaluated on — it shows how
sensitive the fused rule is to the one tunable that matters, not an independently validated
operating point. Writes evaluation/results/qos_threshold_sweep.json.

Usage: python3 evaluation/metrics/qos_threshold_sweep.py [--k6-output .run/k6-tenant-b.jsonl]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "monitors"))
sys.path.insert(0, str(ROOT / "monitors" / "resource-monitor"))
import compute_metrics as cm  # noqa: E402
from common import Event  # noqa: E402
from qos_monitor import percentile  # noqa: E402


def load_points(path: Path) -> list[tuple[float, float]]:
    pts = []
    with open(path) as fh:
        for line in fh:
            if '"http_req_duration"' not in line or '"Point"' not in line:
                continue
            r = json.loads(line)
            pts.append((datetime.fromisoformat(r["data"]["time"]).timestamp(), r["data"]["value"]))
    return sorted(pts)


def replay(points, tenant, ratio, baseline_s=10.0, window_s=10.0, check_s=2.0, reemit_s=5.0) -> list[Event]:
    events: list[Event] = []
    t0 = points[0][0]
    baseline, base_samples = None, []
    rolling: deque = deque()
    last_check, last_emit, breached = t0, -1e9, False
    for t, v in points:
        if baseline is None:
            base_samples.append(v)
            if t - t0 >= baseline_s:
                baseline = percentile(base_samples, 0.95)
            continue
        rolling.append((t, v))
        while rolling and t - rolling[0][0] > window_s:
            rolling.popleft()
        if t - last_check < check_s:
            continue
        last_check = t
        p95 = percentile([x for _, x in rolling], 0.95)
        is_breach = p95 / baseline >= ratio
        if is_breach and (not breached or t - last_emit >= reemit_s):
            last_emit = t
            events.append(Event(source="resource", timestamp=datetime.fromtimestamp(t).astimezone().isoformat(),
                                namespace=tenant, principal=None, peer_namespace=None,
                                resource_type="victim-qos-latency", verb="breach",
                                raw={"rolling_p95_ms": p95, "baseline_p95_ms": baseline}))
        breached = is_breach
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k6-output", default=str(ROOT / ".run" / "k6-tenant-b.jsonl"))
    ap.add_argument("--tenant", default="tenant-b")
    ap.add_argument("--ratios", type=float, nargs="*", default=[1.5, 2.0, 2.5, 3.0])
    ap.add_argument("--output", default=str(ROOT / "evaluation" / "results" / "qos_threshold_sweep.json"))
    args = ap.parse_args()

    base_events = []
    for p in (cm.DEFAULT_CP_EVENTS, cm.DEFAULT_NF_EVENTS, cm.DEFAULT_RES_EVENTS):
        base_events += cm.load_events(Path(p))
    non_qos = [e for e in base_events if e.resource_type != "victim-qos-latency"]
    gt = cm.load_jsonl(Path(cm.DEFAULT_GROUND_TRUTH))
    nc = cm.load_jsonl(Path(cm.DEFAULT_NEGATIVE_CONTROLS))
    points = load_points(Path(args.k6_output))

    rows = []
    print(f"{'ratio':>6} {'TP':>3} {'FN':>3} {'FP(idle)':>9} {'FP(busy)':>9} {'precision':>10} {'recall':>7} {'latency(s)':>11}")
    for ratio in args.ratios:
        events = non_qos + replay(points, args.tenant, ratio)
        r = score_row = cm.score_config("fused-fairness", cm.DETECTOR_CONFIGS["fused-fairness"], events, gt, nc)
        fp = r["false_positives_by_window_type"]
        rows.append({"breach_ratio": ratio, **{k: r[k] for k in (
            "true_positives", "false_negatives", "precision", "recall",
            "mean_detection_latency_seconds", "false_positives_by_window_type")}})
        f = lambda x: f"{x:.2f}" if isinstance(x, float) else "n/a"
        print(f"{ratio:>6} {r['true_positives']:>3} {r['false_negatives']:>3} {fp.get('negative-control', 0):>9} "
              f"{fp.get('negative-control-busy', 0):>9} {f(r['precision']):>10} {f(r['recall']):>7} "
              f"{f(r['mean_detection_latency_seconds']):>11}")
    Path(args.output).write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
