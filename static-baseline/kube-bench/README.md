# Static baseline (Phase 4)

The "static-only" comparison point in the evaluation (proposal §5.2): what a one-time
CIS-benchmark scan catches, versus what the runtime control-plane/data-plane monitors catch.

- `run-kube-bench.sh` — runs kube-bench directly on the kind control-plane node (see script
  header for why: kind's control-plane container has the real kubeadm/kubelet filesystem
  layout kube-bench expects, so `docker cp` + `docker exec` is simpler and more reliable than
  the usual Job + hostPath-mounts setup). Writes `results.txt` (human-readable) and
  `results.json` (structured, used by evaluation scoring later).
- `check_resource_quotas.py` — supplementary check kube-bench doesn't do at all: confirms every
  tenant namespace (`tenant` label) has both a ResourceQuota (with `limits.cpu`/`limits.memory`
  set) and a LimitRange. See docs/risk-log.md #2. Writes `results-resourcequota.json`.

## Latest captured run

66 PASS / 13 FAIL / 51 WARN (CIS benchmark auto-detected as `cis-1.9` for Kubernetes 1.31, run
against the `tenant-lab-control-plane` node — see `results.json` for the full per-check
breakdown). All 3 tenant namespaces pass the supplementary ResourceQuota/LimitRange check
(verified against both the pass and fail case).

Re-run either script any time the cluster changes; both are idempotent.
