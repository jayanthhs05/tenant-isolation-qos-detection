# Unified Tenant-Isolation & QoS-Fairness Detection — Implementation Plan

## Context

This is the IT418 Cloud Computing course project for H S Jayanth (231IT024) and Suvarna G
(231IT077). The proposal (`CC-Project.pdf`) extends a base FSE'25 paper on DAST for
Kubernetes (which only offers findings/recommendations, no artifact, security-only,
no quantitative evaluation) into a working, evaluated system. The core novelty: unify two
failure modes of shared cloud infrastructure — **tenant isolation violations** (security:
Tenant A improperly accessing Tenant B's resources) and **QoS fairness breaches**
(resource management: Tenant A's workload starving Tenant B's promised performance,
the "noisy neighbor" problem) — into one fused detection pipeline, quantitatively evaluated
(precision/recall/FPR/latency) against a static baseline, using injected, repeatable scenario
libraries.

The working directory currently contains only the two source PDFs; this is a from-scratch
build. The plan below is what a two-person team executes over a semester, phased so both
students can work mostly in parallel, with explicit joint checkpoints.

Local environment: Arch Linux (kernel 7.1.8, eBPF-capable), Docker installed. kubectl, kind,
helm, cilium/hubble CLIs, kube-bench, trivy, k6 are **not yet installed** — Phase 0 covers this.
No git repo yet.

## One correction to the proposal to make before/while building

Proposal §6 claims "the same data collection layer can feed both detection tracks"
(Cilium/Hubble). This isn't quite true: Hubble's eBPF probes observe the **network/socket
layer** (great for cross-namespace flow detection = isolation track) but do **not** expose
cgroup CPU-throttling/memory-pressure accounting (needed for the fairness/noisy-neighbor
track) — that data lives in kubelet/cAdvisor, a separate subsystem. Treat the data-plane layer
as **two** subcomponents feeding one fusion layer: an eBPF/Hubble network-flow monitor and
a kubelet/cAdvisor-based resource monitor. Update internal docs (and proposal wording, time
permitting) to reflect this — it doesn't undermine the "one unified pipeline" claim (both still
feed the same fusion/classification layer), it just corrects which raw layer produces which
signal.

## Repository Layout

```
CloudComputing/
├── README.md
├── docs/
│   ├── proposal/                      # move the two existing PDFs here
│   ├── architecture.md
│   ├── risk-log.md                    # the Risks section below, kept living
│   └── evaluation-methodology.md
├── app/                                # multi-tenant demo SaaS app
│   ├── backend/                        # FastAPI REST API, deployed per-tenant-namespace
│   ├── Dockerfile
│   └── k8s/                            # base Deployment/Service manifests (templated per tenant)
├── infra/
│   ├── kind/{kind-config.yaml, audit-policy.yaml}
│   ├── cilium/values.yaml
│   ├── metrics/{metrics-server.yaml, prometheus-values.yaml}
│   └── namespaces/tenant-a/{namespace.yaml, rbac.yaml, networkpolicy.yaml, resourcequota.yaml}  (+ tenant-b, tenant-c)
├── static-baseline/kube-bench/
├── monitors/
│   ├── common/                         # shared canonical event schema (Python package)
│   ├── control-plane-monitor/          # tails audit log → isolation candidate events
│   ├── network-flow-monitor/           # wraps `hubble observe -o json` → boundary-crossing events
│   └── resource-monitor/               # polls kubelet/cAdvisor (or metrics-server) → pressure events
├── fusion/{src/, tests/}                # correlation + classification engine
├── scenarios/
│   ├── isolation/{01-networkpolicy-misconfig, 02-rbac-overpermission, 03-credential-misuse, 04-privilege-escalation, 05-dataplane-only-crossing}/{metadata.yaml, inject.sh, revert.sh}
│   ├── fairness/{01-cpu-hog-batch-job, 02-memory-pressure, 03-quota-bypass, 04-io-noisy-neighbor(stretch)}/...
│   └── driver/run_scenario.py          # apply → dwell → revert; writes ground_truth.jsonl
├── evaluation/
│   ├── harness/run_evaluation.py       # sweeps scenario × detector-config × repetitions
│   ├── metrics/compute_metrics.py      # precision/recall/FPR/latency from ground truth + alerts
│   └── results/
├── load/k6/tenant-load-script.js       # continuous load on victim tenant; publishes rolling p95 latency
├── .github/workflows/trivy-scan.yml    # must live at repo root
└── scripts/{bootstrap-cluster.sh, install-cilium.sh, deploy-app.sh, teardown.sh}
```

## Tech Stack

**Python end-to-end** for every custom service: control-plane monitor, network-flow monitor,
resource monitor, fusion/classification layer, scenario driver, evaluation harness. No Go
needed — the eBPF kernel-side work is already done by Cilium's own agent; the only job is
consuming Hubble's JSON output, which `hubble observe -f -o json` via `subprocess` handles
fine at classroom traffic volume (reach for the gRPC Observer API only if the CLI proves lossy
under load — unlikely). One language lets both students share `monitors/common/`'s
canonical event schema instead of duplicating parsing logic across languages. Demo app:
FastAPI. Interchange format: JSON Lines everywhere (monitor output, ground truth log) —
append-only, greppable, loads straight into `pandas` for scoring. Fusion layer's correlation
window: in-process dict/deque keyed by namespace-pair with time-bounded eviction; no
Redis/SQLite unless correlation logic outgrows that (unlikely at this scale).

## Build Phases (dependency-ordered, with parallelization)

- **Phase 0 — Tooling bootstrap** (both). Install kubectl, kind, helm, cilium CLI, hubble CLI,
  kube-bench, trivy, k6; `git init`; scaffold the repo layout above.
  *Done:* `kind create cluster` succeeds, `kubectl get nodes` Ready.
- **Phase 1 — kind cluster + Cilium CNI + Hubble** (one student; blocks 3, 6, 7). Disable kind's
  default CNI, install Cilium via Helm with Hubble relay/UI/metrics enabled, pin kind node image
  + Cilium chart to a known-compatible version pair (don't use "latest" of both — most fragile
  step in the whole project). *Done:* `cilium status --wait` healthy, `hubble observe` streams
  live pod-to-pod flows.
- **Phase 2 — Demo multi-tenant app** (other student, parallel to Phase 1). FastAPI REST API
  (e.g. a small "tickets/notes" service), one Deployment+Service per tenant namespace, each
  tenant its own DB — infra-level isolation is the point, not app-level multi-tenancy logic.
  *Done:* runs standalone in Docker; manifests ready once namespaces exist.
- **Phase 3 — Tenant isolation primitives** (needs 1+2, joint). Namespace + ServiceAccount +
  Role/RoleBinding, default-deny NetworkPolicy + explicit allows, ResourceQuota + LimitRange
  per tenant. *Done:* `kubectl auth can-i --as=system:serviceaccount:tenant-a:app get pods -n
  tenant-b` → no; cross-namespace curl blocked; over-quota pod creation rejected.
- **Phase 4 — Static baseline (kube-bench)** (anytime after 1; low-effort filler). Run as a Job;
  capture JSON. kube-bench doesn't check ResourceQuota misconfig at all, so write a small
  supplementary check script to make the proposal's "basic resource-quota misconfig" static
  baseline claim literally true. *Done:* report archived under `evaluation/results/`.
- **Phase 5 — Control-plane monitor** (needs 1+3; student A owns). Enable K8s audit logging
  (see Mechanics below), tail it, flag cross-namespace principal→resource access. *Done:*
  manual cross-namespace `kubectl exec` using tenant-a's SA token against tenant-b triggers
  an alert within seconds.
- **Phase 6 — Network-flow monitor** (needs 1+3; student B owns, parallel to 5). Wraps
  `hubble observe`, flags flows crossing tenant boundaries, distinguishing ALLOWED vs
  DROPPED verdicts. *Done:* deliberately misconfigured NetworkPolicy → cross-tenant curl
  succeeds → Hubble records it → monitor alerts.
- **Phase 7 — Resource/fairness monitor** (needs only Phase 1's cluster, not Cilium — this is
  the corrected architecture from above). Polls kubelet cAdvisor (`/stats/summary` or
  `/metrics/cadvisor`) for per-pod CPU/memory usage and throttling counters
  (`container_cpu_cfs_throttled_seconds_total`); metrics-server usage-over-quota as a lower-
  effort fallback signal if standing up Prometheus costs too much time. *Done:* CPU-hog
  scenario in tenant-a produces elevated usage/throttling, and co-located tenant-b's
  k6-measured latency visibly degrades in the same window.
- **Phase 8 — Fusion & classification layer** (needs 5+6+7; joint checkpoint). Canonical event:
  `{event_id, source, timestamp, namespace, principal, peer_namespace, resource_type, verb,
  raw}`. Network-flow events lack SA identity natively — enrich via a Kubernetes-client Pod
  watch for pod→ServiceAccount lookup. Correlation key: (namespace, peer_namespace)
  unordered pair primary, principal secondary, ~10s sliding window. Classification is a
  deterministic rule table (not ML — ground-truth injection makes rules easy to validate):
  control-plane-only or network-flow-only cross-namespace evidence → isolation;
  resource-pressure on tenant A **plus an independent victim-side QoS breach** (k6 p95 >
  ~1.5× baseline) with no cross-namespace access evidence → fairness; both evidence types in
  window → both. Elevated CPU alone is not sufficient fairness evidence. *Done:* one hand-run
  isolation scenario and one hand-run fairness scenario both correctly classified end-to-end.
- **Phase 9 — CI/CD Trivy gate** (independent, no cluster dependency — good filler/quick win
  whenever one student is blocked). GitHub Actions builds the demo app image, scans with
  Trivy, fails on HIGH/CRITICAL. *Done:* workflow fails against a known-vulnerable test image.
- **Phase 10 — Scenario injection libraries** (drafting starts once Phase 3 exists; validated once
  5–8 exist). 5 isolation + 3–4 fairness scenarios, each versioned `metadata.yaml` +
  `inject.sh`/`revert.sh`, driven by `scenarios/driver/run_scenario.py`. Use uniquely-prefixed
  namespaced resources for idempotency; fixed dwell time post-injection (~5–10s for isolation,
  ~30–60s for fairness so throttling stabilizes and k6 gathers samples). Driver appends to
  `ground_truth.jsonl`: `{run_id, scenario_id, category, injection_start_ts, injection_end_ts,
  expected_namespaces, expected_type}` — sole source of truth for scoring.
  *Done:* every scenario runs idempotently via the driver and reverts cleanly.
- **Phase 11 — Evaluation harness & metrics** (needs 8+10). Sweeps scenario × detector-config
  × repetitions; explicitly samples idle/no-injection windows as negative-opportunity periods
  (FPR is ill-posed without this); computes precision/recall/FPR/detection latency per track and
  jointly, plus "QoS impact avoided" for fairness scenarios. Log both "raw signal observed" and
  "alert emitted after correlation" timestamps per monitor so latency decomposes into
  log-tail + rule-eval + correlation-window latency rather than one opaque number.
  Recommend scoping a guaranteed **minimum viable evaluation** up front (e.g. 3 isolation + 2
  fairness scenario types × 5 repetitions) with the rest as stretch, so a defensible complete
  result exists even under time pressure. *Done:* one command reproduces the full metrics
  table promised in proposal §5.2.
- **Phase 12 — Write-up** (last). Architecture diagram, results tables/charts, limitations/
  threats-to-validity (including single-host kernel scheduler contention across kind "nodes",
  and shared-wall-clock latency measurement not holding on a real multi-node deployment),
  refreshed slide deck.

**Parallel summary:** Phase 1 ∥ Phase 2 → Phase 3 (joint) → Phase 5 ∥ (Phase 6 + Phase 7) as
the main split (control-plane vs. data/resource-plane) → Phase 8 (joint) → Phase 11. Phases 4
and 9 are flexible filler slotted in whenever one student is ahead.

## Key Technical Mechanics

- **Audit logs out of kind:** two mount hops, both required. (1) `extraMounts` in the kind
  config bind-mounts a host dir into the kind node container. (2) A `kubeadmConfigPatches`
  block sets `apiServer.extraArgs` (`audit-log-path`, `audit-policy-file`, rotation) **and**
  `apiServer.extraVolumes` to mount that node-local path into the kube-apiserver static pod
  itself — skipping step 2 is the most common failure (apiserver can't see the file even though
  it exists on the node). The audit-policy YAML must exist on the host **before**
  `kind create cluster` runs (kubeadm reads it at apiserver startup). Once wired, `audit.log` is
  directly tailable from the host — no webhook needed. Use `Metadata`-level audit policy
  broadly to keep log volume manageable.
- **Cilium on kind:** `networking.disableDefaultCNI: true` + explicit non-conflicting
  `podSubnet`; nodes stay `NotReady` until Cilium installs (expected, not a bug). Install via
  Helm with `hubble.relay.enabled=true`, `hubble.ui.enabled=true`,
  `hubble.metrics.enabled="{dns,drop,tcp,flow,port-distribution,icmp,http}"`. Skip full
  kube-proxy-replacement mode — fragile under kind, not needed for any detection goal here.
- **Detection latency measurement:** use one host wall-clock across the scenario driver and all
  monitors (kind runs "nodes" as containers on one host, so this is safe here — but flag in the
  report as a threat-to-validity that wouldn't hold on real multi-node clusters).
  `injection_start_ts` is recorded when the injection command is *issued*, not once it takes
  effect.

## Risks Already Flagged (carry into `docs/risk-log.md`)

1. Hubble-for-resource-metrics gap (above) — biggest correction to internal architecture.
2. kube-bench on kind is fiddly (control-plane node is a container, not a standard node);
   budget real debugging time, and kube-bench needs a supplementary script for resource-quota
   checks it doesn't natively do.
3. FPR is ill-posed without explicit negative-control (no-injection) windows — build this into
   the harness from day one.
4. Cilium+kind version compatibility is the single highest-risk setup step — pin versions,
   budget a dedicated session, verify with an explicit eBPF smoke test in Phase 1 (native Linux
   host here, so risk is lower than on Docker Desktop for Mac/Windows, but still verify).
5. Single-host kind means all "nodes" share one physical kernel scheduler — real cgroup
   enforcement still works, but avoid running other heavy processes during evaluation runs.
6. Evaluation scope (5 isolation × 3–4 fairness × 3 detector configs × repetitions) is large for
   two people in a semester — scope a minimum-viable evaluation explicitly (see Phase 11).
7. Auto-remediation (re-applying quotas) stays a strict stretch goal — it's an automated write
   action against the cluster, not needed to satisfy the core proposal claims.

## Milestones (relative weeks)

- **Wk 0 (done):** proposal + slides submitted.
- **Wk 1–2:** Phase 0+1+2 in parallel. *Checkpoint:* cluster healthy w/ Hubble streaming; app
  deployed manually.
- **Wk 3–4:** Phase 3 + Phase 4 + Phase 9 (filler). *Checkpoint:* isolation boundaries
  demonstrated; kube-bench report captured; CI scanning on push.
- **Wk 5–7:** Phase 5 ∥ (6+7), built and tested independently. *Checkpoint (mid-project
  review):* each monitor demonstrated catching a manual test case.
- **Wk 8 (buffer):** Phase 10 scenario libraries authored/hardened; catch-up slot.
- **Wk 9–10:** Phase 8 fusion, joint. *Checkpoint:* one isolation + one fairness scenario
  correctly classified end-to-end.
- **Wk 11–12:** Phase 11 evaluation run, full metrics table produced.
- **Wk 13–14:** Phase 12 write-up, slide deck refresh, buffer.

## Verification (per phase, rolled up)

- Cluster/CNI: `kubectl get nodes`, `cilium status --wait`, `hubble observe` live smoke test.
- Isolation primitives: `kubectl auth can-i` denial test, cross-namespace curl blocked, over-quota
  pod rejected.
- Each monitor: a manually triggered event of the right kind produces a logged alert within
  expected latency, and a manually triggered *unrelated* event does not (basic FP check).
- Fusion: hand-run one isolation + one fairness case → correct, non-overlapping classification.
- End-to-end: `scenarios/driver/run_scenario.py` for one scenario of each type runs, reverts
  cleanly, and appears correctly in `ground_truth.jsonl`.
- Full pipeline: `evaluation/harness/run_evaluation.py` on the minimum-viable scenario set
  produces a metrics table matching proposal §5.2's structure (precision/recall/FPR/latency,
  per-track and joint).
- CI: a PR with a known-vulnerable base image fails the Trivy gate; a clean image passes.
