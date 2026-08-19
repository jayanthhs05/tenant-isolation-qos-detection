#!/usr/bin/env python3
"""Evaluation metrics (Phase 11).

Scores fusion's alerts against scenarios/driver's ground truth, across a matrix of detector
configs, using logs collected by evaluation/harness/run_evaluation.py — a single sweep's
event logs get re-classified under every config listed below without re-running the cluster,
since the ablation is just "which sources does classify_events see" plus the
require_qos_breach flag (fusion/src/classify.py).

Detector configs:
  isolation track: control-plane-only, network-flow-only, fused
  fairness track:  pressure-only (elevated usage alone is "sufficient" — the naive baseline),
                    fused (the real rule: requires an independent victim-side QoS breach too)

For each ground-truth injection window, a track's alert is a match if its category fits the
track (isolation/both for the isolation track, fairness/both for the fairness track), its
tenants overlap the scenario's expected_namespaces, and its first-observed timestamp falls
within [injection_start_ts, injection_end_ts + CORRELATION_WINDOW_SECONDS] (the correlation
window's own lag needs that slack, or every fused alert would spuriously look "late").

False positives are scored ONLY against the harness's explicit negative-control windows
(docs/risk-log.md #3 — FPR is ill-posed without them); alerts outside both ground-truth
windows and negative-control windows are reported separately as "unaccounted" rather than
silently folded into either count.

Usage:
    python3 compute_metrics.py [--ground-truth PATH] [--negative-controls PATH]
                                [--control-plane-events PATH] [--network-flow-events PATH]
                                [--resource-events PATH] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fusion"))  # for `src`
from src import Alert, CORRELATION_WINDOW_SECONDS, classify_events  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "monitors"))  # for `common`
from common import Event  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_GROUND_TRUTH = Path(__file__).resolve().parent.parent / "results" / "ground_truth.jsonl"
DEFAULT_NEGATIVE_CONTROLS = Path(__file__).resolve().parent.parent / "results" / "negative_controls.jsonl"
DEFAULT_CP_EVENTS = ROOT_DIR / "monitors" / "control-plane-monitor" / "events.jsonl"
DEFAULT_NF_EVENTS = ROOT_DIR / "monitors" / "network-flow-monitor" / "events.jsonl"
DEFAULT_RES_EVENTS = ROOT_DIR / "monitors" / "resource-monitor" / "events.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "results" / "metrics.json"

MATCH_SLACK_SECONDS = CORRELATION_WINDOW_SECONDS

DETECTOR_CONFIGS = {
    "control-plane-only": {"track": "isolation", "sources": {"control-plane"}, "require_qos_breach": True},
    "network-flow-only": {"track": "isolation", "sources": {"network-flow"}, "require_qos_breach": True},
    "fused-isolation": {"track": "isolation", "sources": None, "require_qos_breach": True},
    "pressure-only": {"track": "fairness", "sources": None, "require_qos_breach": False},
    "fused-fairness": {"track": "fairness", "sources": None, "require_qos_breach": True},
}


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_events(path: Path) -> list[Event]:
    if not path.exists():
        print(f"[compute_metrics] note: {path} does not exist, treating as empty", file=sys.stderr)
        return []
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(Event.from_json(line))
    return events


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[compute_metrics] note: {path} does not exist, treating as empty", file=sys.stderr)
        return []
    records = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def alert_matches_track(alert: Alert, track: str) -> bool:
    if track == "isolation":
        return alert.category in ("isolation", "both")
    return alert.category in ("fairness", "both")


def alert_matches_ground_truth(alert: Alert, gt: dict, track: str) -> bool:
    if not alert_matches_track(alert, track):
        return False
    alert_tenants = {t for t in alert.tenants if t}
    expected = set(gt["expected_namespaces"])
    min_overlap = 2 if track == "isolation" else 1
    if len(alert_tenants & expected) < min_overlap:
        return False
    window_start = _parse_ts(gt["injection_start_ts"])
    window_end = _parse_ts(gt["injection_end_ts"]) + timedelta(seconds=MATCH_SLACK_SECONDS)
    return window_start <= _parse_ts(alert.first_observed) <= window_end


def score_config(name: str, config: dict, events: list[Event], ground_truth: list[dict],
                  negative_controls: list[dict]) -> dict:
    track = config["track"]
    filtered = [e for e in events if config["sources"] is None or e.source in config["sources"]]
    alerts = classify_events(filtered, require_qos_breach=config["require_qos_breach"])
    track_alerts = [a for a in alerts if alert_matches_track(a, track)]

    track_gt = [gt for gt in ground_truth if gt.get("category") == track]

    tp = 0
    fn = 0
    latencies = []
    correlation_overheads = []
    matched_alert_ids: set[str] = set()
    for gt in track_gt:
        candidates = [a for a in track_alerts if alert_matches_ground_truth(a, gt, track)]
        if candidates:
            tp += 1
            best = min(candidates, key=lambda a: _parse_ts(a.first_observed))
            matched_alert_ids.add(best.alert_id)
            latencies.append((_parse_ts(best.first_observed) - _parse_ts(gt["injection_start_ts"])).total_seconds())
            correlation_overheads.append((_parse_ts(best.fused_at) - _parse_ts(best.first_observed)).total_seconds())
        else:
            fn += 1

    fp = 0
    tn = 0
    for window in negative_controls:
        window_start = _parse_ts(window["start_ts"])
        window_end = _parse_ts(window["end_ts"])
        hit = any(window_start <= _parse_ts(a.first_observed) <= window_end for a in track_alerts)
        if hit:
            fp += 1
        else:
            tn += 1

    accounted_for = matched_alert_ids | {
        a.alert_id for a in track_alerts
        for window in negative_controls
        if _parse_ts(window["start_ts"]) <= _parse_ts(a.first_observed) <= _parse_ts(window["end_ts"])
    }
    unaccounted = len([a for a in track_alerts if a.alert_id not in accounted_for])

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    fpr = fp / (fp + tn) if (fp + tn) else None

    return {
        "detector_config": name,
        "track": track,
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives_in_negative_controls": fp,
        "true_negatives": tn,
        "unaccounted_alerts": unaccounted,
        "precision": precision,
        "recall": recall,
        "false_positive_rate": fpr,
        "mean_detection_latency_seconds": statistics.mean(latencies) if latencies else None,
        "median_detection_latency_seconds": statistics.median(latencies) if latencies else None,
        "mean_correlation_overhead_seconds": statistics.mean(correlation_overheads) if correlation_overheads else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ground-truth", default=str(DEFAULT_GROUND_TRUTH))
    parser.add_argument("--negative-controls", default=str(DEFAULT_NEGATIVE_CONTROLS))
    parser.add_argument("--control-plane-events", default=str(DEFAULT_CP_EVENTS))
    parser.add_argument("--network-flow-events", default=str(DEFAULT_NF_EVENTS))
    parser.add_argument("--resource-events", default=str(DEFAULT_RES_EVENTS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    events: list[Event] = []
    for path_str in (args.control_plane_events, args.network_flow_events, args.resource_events):
        events.extend(load_events(Path(path_str)))
    ground_truth = load_jsonl(Path(args.ground_truth))
    negative_controls = load_jsonl(Path(args.negative_controls))

    print(f"[compute_metrics] {len(events)} events, {len(ground_truth)} ground-truth runs, "
          f"{len(negative_controls)} negative-control windows")

    results = [score_config(name, config, events, ground_truth, negative_controls)
               for name, config in DETECTOR_CONFIGS.items()]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump(results, fh, indent=2)

    print(f"\n{'config':<20} {'track':<10} {'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4} "
          f"{'precision':>10} {'recall':>8} {'FPR':>8} {'latency(s)':>11}")
    for r in results:
        def fmt(x):
            return f"{x:.2f}" if isinstance(x, float) else "n/a"
        print(f"{r['detector_config']:<20} {r['track']:<10} {r['true_positives']:>4} "
              f"{r['false_negatives']:>4} {r['false_positives_in_negative_controls']:>4} "
              f"{r['true_negatives']:>4} {fmt(r['precision']):>10} {fmt(r['recall']):>8} "
              f"{fmt(r['false_positive_rate']):>8} {fmt(r['mean_detection_latency_seconds']):>11}")

    print(f"\n[compute_metrics] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
