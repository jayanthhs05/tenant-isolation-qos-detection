#!/usr/bin/env python3
"""Evaluation harness (Phase 11).

Sweeps scenario x repetitions (plus explicit negative-control idle windows, docs/risk-log.md
#3), driving each run through scenarios/driver/run_scenario.py exactly as a human would —
this script is an orchestrator, not a reimplementation of the driver's apply/dwell/revert
logic. It does NOT run fusion or score anything itself; that split (collect here, score in
evaluation/metrics/compute_metrics.py) mirrors the project's monitor/fusion split and lets the
same collected logs be re-scored under multiple detector-config ablations without re-running
the cluster.

Prerequisites (not started by this script — see evaluation/README.md for the full runbook):
  - the kind cluster up, all three monitors running and writing to their default events.jsonl
  - a k6 load generator + qos_monitor.py running against at least one non-scenario-target
    tenant, for the fairness track's victim-side QoS signal

Usage:
    python3 run_evaluation.py --dry-run                     # print the plan, run nothing
    python3 run_evaluation.py                                # full sweep, all built scenarios
    python3 run_evaluation.py --scenarios scenarios/isolation/01-networkpolicy-misconfig \
        --repetitions 1 --negative-control-windows 1         # a quick smoke-sized subset
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
DRIVER = ROOT_DIR / "scenarios" / "driver" / "run_scenario.py"
DEFAULT_GROUND_TRUTH = Path(__file__).resolve().parent.parent / "results" / "ground_truth.jsonl"
DEFAULT_NEGATIVE_CONTROLS = Path(__file__).resolve().parent.parent / "results" / "negative_controls.jsonl"

# Per-run overhead beyond metadata.yaml's own dwell_seconds: kubectl calls in inject.sh/
# revert.sh, pod scheduling, etc. Measured empirically against this project's own scenarios
# during Phase 10 validation — used only for the --dry-run runtime estimate, not for pacing
# the actual runs (those just run to completion, however long that takes).
ESTIMATED_OVERHEAD_SECONDS = 3.5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_scenarios() -> list[Path]:
    """Every scenario dir under scenarios/{isolation,fairness}/ that has a metadata.yaml —
    skips unbuilt stretch scenarios like fairness/04-io-noisy-neighbor, which is an empty
    directory on purpose (docs/risk-log.md #6)."""
    found = []
    for category_dir in ("isolation", "fairness"):
        base = ROOT_DIR / "scenarios" / category_dir
        if not base.is_dir():
            continue
        for scenario_dir in sorted(base.iterdir()):
            if (scenario_dir / "metadata.yaml").exists():
                found.append(scenario_dir)
    return found


def load_dwell_seconds(scenario_dir: Path) -> float:
    with open(scenario_dir / "metadata.yaml") as fh:
        metadata = yaml.safe_load(fh)
    return float(metadata.get("dwell_seconds", 10))


def run_negative_control(window_seconds: float, output_path: Path) -> None:
    print(f"[run_evaluation] negative-control window: sleeping {window_seconds}s with no injection")
    start_ts = now_iso()
    time.sleep(window_seconds)
    end_ts = now_iso()
    record = {
        "window_type": "negative-control",
        "start_ts": start_ts,
        "end_ts": end_ts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", nargs="*", default=None,
                         help="scenario dirs to include; default is every built scenario (full sweep)")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--negative-control-windows", type=int, default=5)
    parser.add_argument("--negative-control-seconds", type=float, default=20.0)
    parser.add_argument("--ground-truth", default=str(DEFAULT_GROUND_TRUTH))
    parser.add_argument("--negative-controls-output", default=str(DEFAULT_NEGATIVE_CONTROLS))
    parser.add_argument("--shuffle", action="store_true",
                         help="randomize run order instead of grouping by scenario, to avoid systematic ordering bias")
    parser.add_argument("--seed", type=int, default=None, help="random seed for --shuffle, for a reproducible order")
    parser.add_argument("--dry-run", action="store_true", help="print the planned run order and estimated runtime; run nothing")
    args = parser.parse_args()

    scenario_dirs = [Path(s).resolve() for s in args.scenarios] if args.scenarios else discover_scenarios()
    if not scenario_dirs:
        print("[run_evaluation] no scenarios found", file=sys.stderr)
        return 1

    plan: list[tuple[str, Path]] = []  # (kind, scenario_dir or None)
    for scenario_dir in scenario_dirs:
        for _ in range(args.repetitions):
            plan.append(("scenario", scenario_dir))
    for _ in range(args.negative_control_windows):
        plan.append(("negative-control", None))

    if args.shuffle:
        random.Random(args.seed).shuffle(plan)

    total_seconds = 0.0
    for kind, scenario_dir in plan:
        if kind == "scenario":
            total_seconds += load_dwell_seconds(scenario_dir) + ESTIMATED_OVERHEAD_SECONDS
        else:
            total_seconds += args.negative_control_seconds

    print(f"[run_evaluation] {len(scenario_dirs)} scenario type(s) x {args.repetitions} repetition(s) "
          f"+ {args.negative_control_windows} negative-control window(s) = {len(plan)} total runs")
    print(f"[run_evaluation] estimated total runtime: {total_seconds / 60:.1f} minutes")
    for scenario_dir in scenario_dirs:
        print(f"  - {scenario_dir.relative_to(ROOT_DIR)} (dwell={load_dwell_seconds(scenario_dir):.0f}s)")

    if args.dry_run:
        print("[run_evaluation] --dry-run: not executing anything")
        return 0

    for i, (kind, scenario_dir) in enumerate(plan, start=1):
        print(f"\n[run_evaluation] === run {i}/{len(plan)}: {kind}"
              f"{' ' + str(scenario_dir.relative_to(ROOT_DIR)) if scenario_dir else ''} ===")
        if kind == "scenario":
            result = subprocess.run(
                [sys.executable, str(DRIVER), str(scenario_dir), "--ground-truth", args.ground_truth],
            )
            if result.returncode != 0:
                print(f"[run_evaluation] WARNING: scenario run {i} exited {result.returncode}, continuing sweep",
                      file=sys.stderr)
        else:
            run_negative_control(args.negative_control_seconds, Path(args.negative_controls_output))

    print(f"\n[run_evaluation] sweep complete. ground truth: {args.ground_truth}, "
          f"negative controls: {args.negative_controls_output}")
    print("[run_evaluation] next: python3 evaluation/metrics/compute_metrics.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
