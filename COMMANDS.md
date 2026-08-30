# Commands

Every command needed to bring this project up, run an experiment, and tear it down. Requires
Docker; everything else is installed locally into `bin/` (no sudo). Run all commands from the
repo root.

```sh
export PATH="$PWD/bin:$PATH"
```

## Configuration

Every script below reads these from the environment, falling back to the defaults shown —
override only what you need:

| Var | Default | Used by |
|---|---|---|
| `CLUSTER_NAME` | `tenant-lab` | `bootstrap-cluster.sh`, `install-cilium.sh`, `deploy-app.sh`, `up.sh`, `down.sh` |
| `TENANTS` | `tenant-a tenant-b tenant-c` (space-separated) | `deploy-app.sh`, `up.sh` |
| `IMAGE` | `tenant-lab/notes-api:dev` | `deploy-app.sh` |
| `CILIUM_CHART_VERSION` | `1.16.2` | `install-cilium.sh` |
| `PYTHON` | `python3` | `up.sh` |

Example: `CLUSTER_NAME=tenant-lab-2 TENANTS="tenant-a tenant-b" scripts/up.sh`.

## One-shot bring-up

```sh
scripts/up.sh
```

Runs, in order: `install-tools.sh` -> `bootstrap-cluster.sh` (kind cluster + audit logging) ->
`install-cilium.sh` (Cilium/Hubble CNI) -> apply `infra/namespaces/<each tenant>/`
(namespace, RBAC, NetworkPolicy, ResourceQuota) -> `deploy-app.sh` (build + load + deploy the
demo app into every tenant) -> starts all three monitors in the background, logging to `.run/`.
Idempotent — safe to re-run.

## Equivalent step-by-step (what `up.sh` runs)

```sh
scripts/install-tools.sh                                    # kubectl, kind, helm, cilium, hubble, kube-bench, trivy, k6 -> bin/
scripts/bootstrap-cluster.sh                                 # kind cluster "$CLUSTER_NAME" with audit logging, no default CNI
scripts/install-cilium.sh                                    # Cilium + Hubble via helm; waits for cilium status --wait

for t in $TENANTS; do kubectl apply -f "infra/namespaces/$t/"; done

scripts/deploy-app.sh                                         # builds $IMAGE, kind-loads it, deploys to each tenant in $TENANTS

python3 -m pip install -r scenarios/requirements.txt           # PyYAML, needed by driver + evaluation harness

python3 monitors/control-plane-monitor/monitor.py &            # tails apiserver audit log -> events.jsonl
python3 monitors/network-flow-monitor/monitor.py &              # tails hubble observe -> events.jsonl
python3 monitors/resource-monitor/monitor.py &                  # polls kubelet/cAdvisor -> events.jsonl
```

## Tear down

```sh
scripts/down.sh                 # stop monitors, delete the kind cluster
scripts/down.sh --monitors-only # stop monitors, leave the cluster running
```

## Inject a scenario (isolation or fairness violation)

```sh
python3 scenarios/driver/run_scenario.py scenarios/isolation/01-networkpolicy-misconfig \
  --dwell-seconds 30 --ground-truth scenarios/ground_truth.jsonl
```

Any scenario directory under `scenarios/isolation/` or `scenarios/fairness/` that has a
`metadata.yaml` works. `--run-id` overrides the auto-generated run id.

## Fairness scenarios: victim-side QoS load (run alongside the scenario)

```sh
scripts/run-fairness-load.sh                # defaults: TENANT=tenant-b VUS=2 DURATION=4m
scripts/run-fairness-load.sh stop           # stop it early
```

Configurable via env vars — `TENANT`, `VUS`, `DURATION`, `LOCAL_PORT`, `BREACH_RATIO`,
`BASELINE_SECONDS` (all forwarded to k6 / `qos_monitor.py`). Use a tenant namespace that is
*not* the scenario's target as the victim. Heavier/longer load makes a real QoS breach more
likely to register within a scenario's dwell window, e.g.:

```sh
TENANT=tenant-c VUS=8 DURATION=6m scripts/run-fairness-load.sh
```

Equivalent manual steps (what the script runs):

```sh
kubectl -n "$TENANT" port-forward svc/notes-api "$LOCAL_PORT:80" &
TARGET_URL="http://localhost:$LOCAL_PORT/healthz" VUS="$VUS" DURATION="$DURATION" \
  k6 run --out json=.run/k6-$TENANT.jsonl load/k6/tenant-load-script.js &
python3 monitors/resource-monitor/qos_monitor.py --k6-output .run/k6-$TENANT.jsonl \
  --tenant "$TENANT" --breach-ratio "$BREACH_RATIO" --baseline-seconds "$BASELINE_SECONDS"
```

## Fusion: correlate + classify monitor output into alerts

```sh
python3 fusion/src/run_fusion.py
# equivalent to:
python3 fusion/src/run_fusion.py \
  --control-plane-events monitors/control-plane-monitor/events.jsonl \
  --network-flow-events monitors/network-flow-monitor/events.jsonl \
  --resource-events monitors/resource-monitor/events.jsonl \
  --output fusion/alerts.jsonl
```

## Evaluation harness (sweep scenarios x repetitions, score against ground truth)

Prerequisites: cluster up, all three monitors running, and (for fairness scenarios) a k6 load
generator + `qos_monitor.py` running against a non-target tenant — i.e. everything `up.sh`
starts, plus the QoS load block above.

```sh
python3 evaluation/harness/run_evaluation.py --dry-run     # print the sweep plan, run nothing
python3 evaluation/harness/run_evaluation.py                # full sweep, all built scenarios
python3 evaluation/harness/run_evaluation.py \
  --scenarios scenarios/isolation/01-networkpolicy-misconfig \
  --repetitions 1 --negative-control-windows 1               # quick smoke test

python3 evaluation/metrics/compute_metrics.py                # precision/recall/FPR/latency from the collected results
```

## Static baseline (comparison point — no cluster changes)

```sh
static-baseline/kube-bench/run-kube-bench.sh
python3 static-baseline/kube-bench/check_resource_quotas.py
```

## Useful one-offs

```sh
kubectl get nodes -o wide                                    # cluster health
kubectl get pods -A -l app=notes-api -o wide                  # demo app rollout status
cilium status                                                  # Cilium health
hubble observe --server localhost:4245 --last 20               # requires the port-forward install-cilium.sh sets up, or your own
tail -f .run/*.log                                             # monitor output (started by up.sh)
trivy image tenant-lab/notes-api:dev                            # vulnerability scan (also runs in CI, .github/workflows/trivy-scan.yml)
```
