# Evaluation Methodology — Runbook

Phase 11 is implemented (`evaluation/harness/run_evaluation.py`,
`evaluation/metrics/compute_metrics.py`, `load/k6/tenant-load-script.js`,
`monitors/resource-monitor/qos_monitor.py`) and the full sweep has been run (2026-10-05, results
in `docs/project-report.md` §6). It takes roughly 35 minutes of live cluster time including the
20 s cooldown after each run (`--cooldown-seconds`), which keeps lingering contention from one
fairness run from bleeding into the next. Fairness scenarios need the worker node pinned to a small
cpuset (`scripts/constrain-node.sh`, run by `scripts/up.sh`) — see `docs/risk-log.md` #15.

## What "full sweep" means here

All 8 built scenarios (5 isolation + 3 fairness; `scenarios/fairness/04-io-noisy-neighbor` is
an unbuilt stretch goal, see `docs/risk-log.md` #6) x 5 repetitions each, plus 5 idle and 5 benign-busy negative-control
idle windows (`docs/risk-log.md` #3 — false-positive rate is meaningless without explicit
no-injection windows to measure it against).

Unlike the original proposal's "scenario x detector-config x repetitions" framing might
suggest, **detector configs don't need separate scenario runs.** `run_evaluation.py` only
executes each scenario once per repetition; `compute_metrics.py` re-classifies the same
collected event logs under every detector config afterward (it's just a source filter + one
boolean flag on `classify_events`, see `fusion/src/classify.py`). So the full sweep is a single
pass, not one pass per config.

**Detector configs scored:**
- Isolation track: `control-plane-only`, `network-flow-only`, `fused-isolation`
- Fairness track: `pressure-only` (naive — elevated usage alone is "sufficient"), `fused-fairness`
  (the real rule — requires an independent victim-side QoS breach too)

The isolation configs show what each individual signal source catches on its own vs. fused.
The fairness configs are the more interesting comparison: they're expected to show
`pressure-only` with materially worse precision than `fused-fairness`, because ordinary
legitimate load produces resource-pressure blips that `pressure-only` can't distinguish from
real noisy-neighbor behavior — that's the actual quantitative case for requiring the QoS-breach
correlation, not just an assertion in the architecture doc.

## Prerequisites

1. Cluster up and healthy: `kubectl get nodes`, `cilium status --wait`, all three tenant pods
   `Running` (`scripts/bootstrap-cluster.sh` / `scripts/install-cilium.sh` /
   `scripts/deploy-app.sh` if starting fresh).
2. `pip install -r scenarios/requirements.txt` (PyYAML — the scenario driver and the
   evaluation harness both parse `metadata.yaml`).
3. Avoid running other heavy processes on the host during the run — all three kind "nodes"
   share one physical kernel scheduler (`docs/risk-log.md` #5), and the fairness scenarios are
   explicitly measuring resource contention.

## Step by step

Run each of the following in its own terminal (or background with `&` + `nohup`), from the
repo root with `./bin` on `PATH`.

**1. Start the three monitors** (each tails/polls continuously and appends to its own
`events.jsonl` — `docs/risk-log.md` #9 applies if a monitor needs to survive audit-log
rotation during a long run):

```bash
python3 -u monitors/control-plane-monitor/monitor.py &
python3 -u monitors/network-flow-monitor/monitor.py &
python3 -u monitors/resource-monitor/monitor.py &
```

**2. Start the k6 load generator + QoS-breach detector against one tenant that no fairness
scenario ever injects into directly** (all fairness scenarios in this project target
tenant-a — see `scenarios/fairness/*/metadata.yaml` — so tenant-b is the natural "innocent
bystander" victim):

```bash
kubectl -n tenant-b port-forward svc/notes-api 18080:80 &
TARGET_URL=http://localhost:18080/healthz VUS=2 DURATION=20m \
  k6 run --out json=/tmp/k6-tenant-b.jsonl load/k6/tenant-load-script.js &
python3 -u monitors/resource-monitor/qos_monitor.py \
  --k6-output /tmp/k6-tenant-b.jsonl --tenant tenant-b &
```

Give this ~15s before starting the sweep so `qos_monitor.py`'s baseline window
(`--baseline-seconds`, default 10s) settles on quiet traffic before any scenario runs.

**3. (Optional) preview the plan without spending any cluster time:**

```bash
python3 evaluation/harness/run_evaluation.py --dry-run
```

**4. Run the full sweep** (~50 minutes; add `--shuffle --seed N` for a randomized,
reproducible run order instead of grouping by scenario):

```bash
python3 evaluation/harness/run_evaluation.py
```

Run it in a real terminal (not redirected to a file) to get a live view: a countdown bar during
each scenario's dwell and each negative-control window (the longest silent stretches
otherwise), plus an overall `i/total` progress bar with elapsed time and ETA after every run.
Piped or redirected output falls back to a plain progress line printed every couple of seconds
instead of the carriage-return redraw, so log files stay readable.

This writes `evaluation/results/ground_truth.jsonl` and
`evaluation/results/negative_controls.jsonl` as it goes — safe to `Ctrl+C` and resume later
with a fresh invocation (it appends, doesn't overwrite; ground truth from a partial run is
still valid ground truth for whatever scenarios did complete).

**5. Stop the monitors and k6/qos_monitor** (`Ctrl+C` each, or `pkill -f monitor.py`,
`pkill -f "k6 run"`, `pkill -f qos_monitor.py`, `pkill -f "port-forward.*notes-api"`).

**6. Score the run:**

```bash
python3 evaluation/metrics/compute_metrics.py
```

Prints a precision/recall/FPR/detection-latency table per detector config to stdout and writes
the same data as `evaluation/results/metrics.json`.

## Reading the output

- **precision / recall**: standard definitions, computed per detector config against its
  track's ground-truth runs and negative-control windows only.
- **false_positive_rate**: `FP / (FP + TN)`, where a negative-control window counts as one FP
  if *any* matching-category alert's `first_observed` falls inside it, else one TN. This is
  deliberately per-window, not per-alert, to keep FPR framed as "rate over opportunities to
  falsely alarm" rather than raw alert volume.
- **mean/median_detection_latency_seconds**: `alert.first_observed - injection_start_ts` for
  matched true positives — the time from when the scenario driver *issued* the injection
  command to when the earliest matching raw signal was observed.
- **mean_correlation_overhead_seconds**: `alert.fused_at - alert.first_observed` — the
  additional lag fusion's correlation window itself adds on top of raw signal latency, per
  `docs/architecture.md`'s latency-decomposition design.
- **unaccounted_alerts**: alerts that landed outside every ground-truth window and every
  negative-control window (e.g. during the ~15s k6-baseline settle time, or between the sweep
  finishing and monitors being stopped). Reported for transparency, not folded into FPR.

## Re-scoring without re-running anything

Since `compute_metrics.py` re-classifies from the raw event logs, any change to
`fusion/src/classify.py`'s rules (e.g. a different `CORRELATION_WINDOW_SECONDS`, or a new
detector config added to `DETECTOR_CONFIGS`) can be re-scored against an existing sweep's logs
with no new cluster time — just re-run `compute_metrics.py`. The event logs and
`ground_truth.jsonl`/`negative_controls.jsonl` are the durable artifacts of a sweep; delete and
re-run `run_evaluation.py` only when you actually want fresh injections.

## Benign-busy controls and threshold analysis

Idle negative-control windows can't distinguish a naive "pressure alone" fairness rule from the
fused rule (neither fires on silence). `run_evaluation.py --busy-control-windows N` (default 5) adds
windows where a well-behaved tenant saturates its own small CPU limit
(`scenarios/controls/busy-benign/`); `metrics.json` reports false positives per window type
(`false_positives_by_window_type`). After a sweep, `evaluation/metrics/qos_threshold_sweep.py`
replays the k6 output at several `--breach-ratio` values and re-scores `fused-fairness`; treat it as
post-hoc sensitivity analysis and validate any chosen value on a fresh sweep (see
`docs/project-report.md` §6). Alerts are scored per track using `isolation_first_observed` /
`fairness_first_observed`, not the alert-wide `first_observed`.
