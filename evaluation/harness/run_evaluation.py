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
import os
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


def _dwell_with_progress(seconds: float, label: str, bar_width: int = 24) -> None:
    """Same live-countdown behavior as scenarios/driver/run_scenario.py's dwell_with_progress
    — kept as its own small copy rather than a shared import, since the two scripts don't
    otherwise share a module and this is the only piece either needs from the other."""
    interactive = sys.stdout.isatty()
    start = time.monotonic()
    last_plain_print = -999.0
    while True:
        elapsed = min(time.monotonic() - start, seconds)
        if interactive:
            filled = int(bar_width * elapsed / seconds) if seconds else bar_width
            bar = "#" * filled + "-" * (bar_width - filled)
            sys.stdout.write(f"\r[run_evaluation] {label} [{bar}] {elapsed:4.1f}s / {seconds:.1f}s")
            sys.stdout.flush()
        elif elapsed - last_plain_print >= 2.0 or elapsed >= seconds:
            print(f"[run_evaluation] {label}: {elapsed:.1f}s / {seconds:.1f}s")
            last_plain_print = elapsed
        if elapsed >= seconds:
            break
        time.sleep(min(0.2, seconds - elapsed))
    if interactive:
        sys.stdout.write("\n")
        sys.stdout.flush()


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes}m{secs:02d}s"


def print_overall_progress(completed: int, total: int, sweep_start: float, bar_width: int = 30) -> None:
    elapsed = time.monotonic() - sweep_start
    pace = elapsed / completed if completed else 0.0
    eta = pace * (total - completed)
    fraction = completed / total if total else 1.0
    filled = int(bar_width * fraction)
    bar = "#" * filled + "-" * (bar_width - filled)
    print(f"[run_evaluation] overall: [{bar}] {completed}/{total} ({fraction:.0%})  "
          f"elapsed {_format_duration(elapsed)}  ETA {_format_duration(eta)}")


BUSY_CONTROL_DIR = ROOT_DIR / "scenarios" / "controls" / "busy-benign"


def _run_control_script(name: str) -> None:
    subprocess.run(
        ["bash", str(BUSY_CONTROL_DIR / name)], check=False,
        env={**os.environ, "PATH": f"{ROOT_DIR}/bin:{os.environ.get('PATH', '')}", "ROOT_DIR": str(ROOT_DIR)},
    )


def run_negative_control(window_seconds: float, output_path: Path, busy: bool = False) -> None:
    """Idle windows (nothing happens) or, with busy=True, a *benign busy* window: a well-behaved
    tenant saturating its own small CPU limit. Idle windows alone can't separate the naive
    pressure-only rule from the fused rule (neither fires on silence); busy windows can."""
    start_ts = now_iso()
    if busy:
        _run_control_script("inject.sh")
    try:
        _dwell_with_progress(window_seconds, "negative-control window (benign busy tenant)" if busy
                             else "negative-control window (no injection)")
    finally:
        end_ts = now_iso()
        if busy:
            _run_control_script("revert.sh")
    record = {
        "window_type": "negative-control-busy" if busy else "negative-control",
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
    parser.add_argument("--busy-control-windows", type=int, default=5,
                         help="benign-busy negative-control windows (a well-behaved tenant saturating its own limit)")
    parser.add_argument("--fairness-cooldown-seconds", type=float, default=45.0,
                         help="longer cooldown after fairness runs: victim QoS-breach events linger past the "
                              "injection and would otherwise merge into the next run's alert")
    parser.add_argument("--negative-control-seconds", type=float, default=20.0)
    parser.add_argument("--cooldown-seconds", type=float, default=20.0,
                         help="idle gap after each scenario run so the victim's latency recovers before the next "
                              "window (otherwise lingering contention from one run bleeds into the next)")
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
    for _ in range(args.busy_control_windows):
        plan.append(("busy-control", None))

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

    sweep_start = time.monotonic()
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
            run_negative_control(args.negative_control_seconds, Path(args.negative_controls_output),
                                 busy=(kind == "busy-control"))
        is_fairness = kind in ("scenario", "busy-control") and (
            kind == "busy-control" or scenario_dir.parent.name == "fairness")
        cooldown = args.fairness_cooldown_seconds if is_fairness else args.cooldown_seconds
        if kind != "negative-control" and i < len(plan) and cooldown > 0:
            _dwell_with_progress(cooldown, "cooldown")
        print_overall_progress(i, len(plan), sweep_start)

    print(f"\n[run_evaluation] sweep complete. ground truth: {args.ground_truth}, "
          f"negative controls: {args.negative_controls_output}")
    print("[run_evaluation] next: python3 evaluation/metrics/compute_metrics.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
