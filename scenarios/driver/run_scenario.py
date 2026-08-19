#!/usr/bin/env python3
"""Scenario driver (Phase 10).

Runs one scenario end-to-end: apply -> dwell -> revert, and appends a ground-truth record to
scenarios/ground_truth.jsonl. This is the sole source of truth the evaluation harness
(Phase 11) scores fusion's alerts against — everything the monitors/fusion layer report is
checked against what this driver actually did and when, not against any monitor's own idea of
what happened.

Each scenario directory (scenarios/isolation/<name>/, scenarios/fairness/<name>/) contains:
  metadata.yaml   category, description, expected_namespaces, expected_type, dwell_seconds
  inject.sh       idempotent — safe to re-run without manual cleanup first
  revert.sh       always attempted, even if inject.sh or the dwell period errors

Usage:
    python3 run_scenario.py <scenario_dir> [--dwell-seconds N] [--ground-truth PATH] [--run-id ID]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_GROUND_TRUTH = Path(__file__).resolve().parent.parent / "ground_truth.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_metadata(scenario_dir: Path) -> dict:
    metadata_path = scenario_dir / "metadata.yaml"
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} not found")
    with open(metadata_path) as fh:
        return yaml.safe_load(fh)


def run_script(scenario_dir: Path, script_name: str) -> None:
    script_path = scenario_dir / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} not found")
    print(f"[run_scenario] running {script_path}")
    subprocess.run(
        ["bash", str(script_path)],
        cwd=str(scenario_dir),
        check=True,
        env={**os.environ, "PATH": f"{ROOT_DIR}/bin:{os.environ.get('PATH', '')}", "ROOT_DIR": str(ROOT_DIR)},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario_dir", help="e.g. scenarios/isolation/01-networkpolicy-misconfig")
    parser.add_argument("--dwell-seconds", type=float, default=None,
                         help="override metadata.yaml's dwell_seconds")
    parser.add_argument("--ground-truth", default=str(DEFAULT_GROUND_TRUTH))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    scenario_dir = Path(args.scenario_dir).resolve()
    metadata = load_metadata(scenario_dir)
    scenario_id = metadata.get("id", scenario_dir.name)
    dwell_seconds = args.dwell_seconds if args.dwell_seconds is not None else metadata.get("dwell_seconds", 10)
    run_id = args.run_id or str(uuid.uuid4())

    print(f"[run_scenario] scenario={scenario_id} run_id={run_id} dwell={dwell_seconds}s")

    injection_start_ts = now_iso()
    injection_end_ts = None
    try:
        run_script(scenario_dir, metadata.get("inject", "inject.sh"))
        print(f"[run_scenario] injected, dwelling for {dwell_seconds}s")
        time.sleep(dwell_seconds)
    finally:
        injection_end_ts = now_iso()
        try:
            run_script(scenario_dir, metadata.get("revert", "revert.sh"))
        except subprocess.CalledProcessError as exc:
            print(f"[run_scenario] WARNING: revert.sh failed ({exc}) — scenario may have left "
                  f"cluster state behind, check {scenario_dir} manually", file=sys.stderr)

    record = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "category": metadata.get("category"),
        "injection_start_ts": injection_start_ts,
        "injection_end_ts": injection_end_ts,
        "expected_namespaces": metadata.get("expected_namespaces", []),
        "expected_type": metadata.get("expected_type"),
    }

    ground_truth_path = Path(args.ground_truth)
    ground_truth_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ground_truth_path, "a") as fh:
        fh.write(json.dumps(record) + "\n")

    print(f"[run_scenario] done, ground truth appended to {ground_truth_path}")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
