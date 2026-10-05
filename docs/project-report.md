# Unified Detection of Tenant-Isolation Violations and QoS Fairness Breaches in Multi-Tenant Cloud Platforms

**Course:** IT418 — Cloud Computing
**Authors:** H S Jayanth (231IT024), Suvarna G (231IT077)

---

## Abstract

Multi-tenancy — many customers safely and fairly sharing one pool of infrastructure — is
the defining economic and architectural property of cloud computing, yet most runtime
detection tooling for Kubernetes treats it as an afterthought. Existing work, including the
base paper this project extends (*"Dynamic Application Security Testing for Kubernetes
Deployment: An Experience Report from Industry"*, FSE '25), evaluates Kubernetes
deployments for generic security misconfiguration but stops there: it offers no implemented
artifact, no quantitative evaluation, and — most importantly — only one notion of
"violation." A shared cluster can fail its tenants in two distinct ways: an **isolation
violation**, where one tenant improperly accesses another's resources (a security problem),
and a **fairness/QoS violation**, where one tenant's workload starves another's promised
performance, the classic "noisy neighbor" problem (a resource-management problem). This
project designs, builds, and quantitatively evaluates a working system that unifies both
failure modes under one fused detection pipeline: a control-plane monitor (Kubernetes audit
logs), a data-plane monitor (eBPF network flows via Cilium/Hubble), and a resource-plane
monitor (kubelet/cAdvisor CPU/memory pressure), correlated by a deterministic fusion and
classification layer and benchmarked against a static-only baseline (kube-bench). This
report covers the project's motivation, related work, objectives, and the results of the
first objective — construction of the multi-tenant testbed and its static configuration
baseline — which establishes both the isolation guarantees the runtime monitors are built
to watch over and the quantitative "static-only" comparison point the rest of the
evaluation is measured against.

## 1. Introduction

Kubernetes has become the de facto substrate for delivering cloud services, and in almost
every production deployment that substrate is shared: multiple tenants — teams, customers,
or workloads — run side by side on the same cluster, separated only by whatever
namespace, RBAC, network, and quota boundaries an operator configures. This sharing is
precisely what makes cloud computing economically viable (resource pooling), but it is also
exactly where cloud platforms fail their tenants when boundaries are misconfigured or
insufficiently enforced. Two distinct failure modes follow from the same root cause —
imperfect enforcement of tenant boundaries:

- **Isolation failures (security):** a principal belonging to one tenant improperly accesses
  a resource — a pod, a service, a secret, an API object — scoped to another tenant.
- **Fairness/QoS failures (resource management):** one tenant's workload consumes shared
  CPU, memory, or I/O capacity beyond its fair share, degrading a co-located tenant's
  measured performance even though no access boundary was crossed.

Static configuration analysis (e.g., CIS benchmark scanners) catches some misconfigurations
before a workload ever runs, but cannot catch violations that only manifest once a cluster is
under live traffic and real workloads — an argument the base paper makes for Kubernetes
security in general, but never extends to the resource-management half of multi-tenancy at
all. This project takes that argument, reframes it explicitly around cloud computing's
defining property, and builds a working artifact that detects **both** halves of the problem
from one shared telemetry pipeline, evaluated quantitatively (precision, recall,
false-positive rate, detection latency) rather than only described.

## 2. Literature Survey

### 2.1 Base paper: DAST for Kubernetes (FSE '25)

Shamim, Hu, and Rahman's industry experience report evaluates three Dynamic Application
Security Testing (DAST) tools against a live, industry-partner Kubernetes deployment and
finds thousands of CIS-recommended configuration violations that static analysis alone
missed. Its central, and correct, argument is that static checks cannot catch violations that
only manifest once a cluster is running under real workloads, and it recommends DAST as a
practitioner-facing complement to static review. Its scope, however, is narrow along three
axes directly relevant to this project:

1. **No implemented artifact** — the paper documents findings and a recommendation; it does
   not design, build, or release a detection tool.
2. **Kubernetes treated as a generic deployment substrate**, not engaged with as a
   multi-tenant resource-pooling platform — the property that actually distinguishes cloud
   computing from ordinary application deployment.
3. **Single-dimension notion of "violation"** — only security misconfiguration is
   considered; resource-fairness/noisy-neighbor failures, which are just as fundamental to
   shared infrastructure, are out of scope.
4. **No quantitative evaluation** — no precision, recall, false-positive rate, or detection
   latency is reported, and there is no controlled comparison against a static baseline.
5. **Single signal source** — dynamic detection in this space typically relies on
   control-plane (API/audit) signals alone, missing violations visible only at the
   data/resource plane.

### 2.2 eBPF-based Kubernetes runtime security

Tools such as Falco and Cilium/Tetragon provide general-purpose eBPF-based runtime anomaly
detection for Kubernetes — syscall- and network-level visibility used for intrusion and
policy-violation detection. Cilium itself (via its Hubble observability layer) is used
directly in this project as the network/socket-layer eBPF data source. These tools are
mature and effective at what they target, but treat "runtime security" generically; none
frame their detection specifically around cross-tenant boundary violations in a
multi-tenant cluster, and none pair security detection with a resource-fairness signal.

### 2.3 Cloud resource fairness and the noisy-neighbor problem

A separate line of work addresses cloud resource fairness and noisy-neighbor mitigation —
detecting and containing workloads that starve co-located tenants of CPU, memory, or I/O
bandwidth. This work is generally concerned with scheduling and enforcement (e.g.,
cgroup-based throttling, QoS classes), not with correlating fairness breaches against an
independent, victim-side performance signal as evidence, and it does not connect to
tenant-isolation security detection at all.

### 2.4 The gap

Recent (2025) work surveys eBPF-based Kubernetes security tooling and explores federated
eBPF-based anomaly detection, but — as with the base paper — treats security and
resource-fairness monitoring as separate concerns with separate pipelines. No identified
IEEE/ACM publication unifies tenant-isolation security detection and tenant fairness/QoS
detection under a single fused control-plane/data-plane pipeline, evaluated quantitatively
across both failure modes. This is the gap this project fills.

## 3. Motivation

Three observations motivate this project directly:

1. **Multi-tenancy is not a peripheral concern for cloud computing — it is the concern.** A
   cloud provider's central operational question is not "is this cluster misconfigured?" in
   the abstract, but "are my tenants safely *and* fairly sharing this pool of
   infrastructure?" Existing tooling answers only the security half of that question.
2. **Isolation and fairness violations share a root cause but are invisible to each other's
   detectors.** A control-plane audit-log monitor sees who asked to access what, but is blind
   to a CPU-hogging batch job that never makes an unauthorized API call. An eBPF network
   monitor sees cross-namespace traffic, but not cgroup CPU throttling or memory pressure —
   that data lives in an entirely different subsystem (kubelet/cAdvisor). A cloud operator
   who deploys only one of these misses half of what "safe multi-tenancy" requires.
3. **Nobody has quantitatively evaluated a unified pipeline for both.** Without precision,
   recall, false-positive rate, and detection latency numbers — measured against a
   structured, repeatable, ground-truth scenario library and a static baseline — claims about
   detection effectiveness remain anecdotal. This project treats that evaluation as a
   first-class deliverable, not an afterthought.

## 4. Objectives

The project is organized around the following objectives, built and validated in
dependency order:

1. **Construct a multi-tenant Kubernetes testbed with enforced isolation boundaries, and
   establish a static configuration baseline against it.** Stand up a local kind cluster with
   Cilium/Hubble (eBPF CNI) and Kubernetes audit logging; deploy a demo multi-tenant SaaS
   application across three tenant namespaces, each with its own ServiceAccount/Role/
   RoleBinding, default-deny NetworkPolicy with explicit allows, and ResourceQuota/
   LimitRange; and run kube-bench against CIS Kubernetes benchmarks (plus a supplementary
   resource-quota check kube-bench does not perform) as the "static-only" comparison point
   for the rest of the evaluation.
2. **Build a control-plane monitor for isolation violations.** Tail Kubernetes audit logs in
   near real time and flag any principal from one tenant's namespace accessing a resource
   scoped to a different tenant.
3. **Build data-plane and resource-plane monitors for isolation and fairness signals.** Wrap
   `hubble observe` to flag network flows crossing tenant namespace boundaries (isolation),
   and poll kubelet/cAdvisor for per-pod CPU throttling and memory pressure attributable to a
   tenant's workload spilling into another tenant's share (fairness/QoS).
4. **Build a fusion and classification layer that unifies both tracks.** Correlate
   control-plane and data/resource-plane events by namespace pair, principal, and a bounded
   time window, and classify each correlated anomaly as an isolation violation, a fairness
   violation, or both — requiring an independent, victim-side QoS breach (not just elevated
   self-usage) as evidence for any fairness classification.
5. **Add a CI/CD security gate.** Scan container images for known vulnerabilities
   (Trivy) before deployment, as a preventive complement to the runtime tracks.
6. **Build repeatable scenario injection libraries and a quantitative evaluation harness.**
   Author versioned, idempotent isolation- and fairness-violation scenarios with ground-truth
   logging, and a harness that sweeps scenario × detector configuration × repetition
   (including explicit negative-control/idle windows, without which false-positive rate is
   ill-posed) to compute precision, recall, false-positive rate, and detection latency per
   track and jointly, compared against the static baseline from Objective 1.

## 5. Results of Objective 1

Objective 1 covers both halves of the testbed: (a) the isolation primitives that the runtime
monitors are built to watch over, and (b) the static baseline scan that quantifies what a
one-time, pre-runtime check can and cannot catch on this same cluster.

### 5.1 Testbed construction

A local `kind` cluster was brought up with the default CNI disabled and Cilium installed in
its place (Hubble relay, UI, and per-protocol flow metrics enabled), giving live eBPF-based
network observability (`hubble observe`) confirmed against real pod-to-pod traffic.
Kubernetes API-server audit logging was wired through to a host-readable `audit.log` via a
two-hop `kind` `extraMounts` + `kubeadmConfigPatches` configuration. A demo multi-tenant
FastAPI application ("notes-api") was deployed once per tenant namespace, each with its own
backing store, so that isolation is enforced at the infrastructure level rather than by
application logic.

Three tenant namespaces (`tenant-a`, `tenant-b`, `tenant-c`) were each provisioned with:

- A dedicated **ServiceAccount** plus a **Role**/**RoleBinding** scoped to read-only access
  (`get`/`list`/`watch` on pods, services, configmaps) within that namespace only — the
  identity the tenant's own application pod runs as, and the principal isolation scenarios
  later attempt to misuse against other tenants.
- A **default-deny NetworkPolicy** (both `Ingress` and `Egress`) with explicit allows for
  same-namespace pod-to-pod traffic and DNS egress to `kube-system` only — every other
  cross-namespace flow is exactly what the network-flow monitor watches for.
- A **ResourceQuota** (`requests.cpu: 1`, `requests.memory: 512Mi`, `limits.cpu: 2`,
  `limits.memory: 1Gi`, `pods: 10`) and a **LimitRange** with sane per-container request/limit
  defaults, so a pod manifest that omits its own requests/limits does not silently become
  unbounded.

These boundaries were manually verified as effective before any monitor was built on top of
them: `kubectl auth can-i` denies a tenant-a principal read access into tenant-b; a direct
cross-namespace request to a peer tenant's pod is dropped by the default-deny NetworkPolicy;
and a pod creation request exceeding a tenant's ResourceQuota is rejected by the API server.

### 5.2 Static baseline: kube-bench (CIS Kubernetes benchmark)

`kube-bench` was run directly against the `kind` control-plane node (auto-detected profile
`cis-1.9` for Kubernetes 1.31), producing the following category breakdown from the latest
captured run:

| Category      | PASS | FAIL | WARN | INFO |
|---------------|-----:|-----:|-----:|-----:|
| Master        |   40 |    7 |   12 |    0 |
| etcd          |    7 |    0 |    0 |    0 |
| Control Plane |    1 |    0 |    4 |    0 |
| Node          |   16 |    2 |    6 |    0 |
| Policies      |    2 |    4 |   29 |    0 |
| **Total**     | **66** | **13** | **51** | **0** |

Of the 13 automated failures, several are directly relevant to the multi-tenant boundaries
this project depends on and later monitors at runtime — e.g., *5.1.3 Minimize wildcard use in
Roles and ClusterRoles*, *5.1.5 Ensure that default service accounts are not actively used*,
and *5.1.6 Ensure that Service Account Tokens are only mounted where necessary* all failed
against the cluster's default/system service accounts (not the project's own per-tenant
RBAC, which is deliberately scoped and unprivileged). The large **Policies** category (2
PASS / 4 FAIL / 29 WARN) is where kube-bench is weakest for this project's purposes: most of
its NetworkPolicy, Pod Security Standards, and namespace-boundary checks (e.g., *5.3.2 Ensure
that all Namespaces have NetworkPolicies defined*, *5.7.1 Create administrative boundaries
between resources using namespaces*) are marked `Manual`/`WARN` rather than automatically
verified — kube-bench can note that a control point *should* be checked, but cannot itself
confirm this cluster's specific default-deny NetworkPolicies or per-tenant RBAc scoping are
actually correct and in force. That confirmation instead came from the direct verification
in §5.1, and is precisely the kind of runtime behavior Objectives 2–4 are built to monitor
continuously rather than check once.

### 5.3 Supplementary check: resource-quota misconfiguration

kube-bench does not check for missing or misconfigured `ResourceQuota`/`LimitRange` objects
at all — a gap identified while scoping this project (`docs/risk-log.md` #2), since it is
needed to make the proposal's "basic resource-quota misconfiguration" static-baseline claim
literally true. A supplementary script (`static-baseline/kube-bench/check_resource_quotas.py`)
was written to close this gap: it confirms, for every namespace carrying a `tenant` label,
that both a `ResourceQuota` (with `limits.cpu`/`limits.memory` explicitly set) and a
`LimitRange` are present. Run against the live cluster:

| Namespace | ResourceQuota (cpu+mem limits) | LimitRange | Result |
|-----------|:---:|:---:|:---:|
| tenant-a  | ✅ | ✅ | PASS |
| tenant-b  | ✅ | ✅ | PASS |
| tenant-c  | ✅ | ✅ | PASS |

All three tenant namespaces pass. The script was also verified against a deliberately
introduced failing case (a namespace missing its quota) to confirm it correctly flags a
negative before being trusted as part of the static baseline.

### 5.4 Interpretation

Objective 1's results establish two things the remaining objectives build directly on:

- **The isolation boundaries the runtime monitors are meant to watch are real and enforced**,
  not just declared in YAML — verified by direct negative tests (denied RBAC call, dropped
  cross-namespace flow, rejected over-quota pod), giving later isolation-scenario injections
  (Objective 6) a boundary that must actually be *misconfigured* to be crossed, rather than
  one that was already open.
- **The static-only baseline is real, not a straw man.** 66/13/51 (PASS/FAIL/WARN) plus a
  clean 3/3 supplementary quota check is a legitimate one-time compliance snapshot — but the
  results in §5.2 also make concrete the base paper's central claim (static checks can't
  catch what only manifests at runtime): the majority of the checks most relevant to
  tenant-boundary correctness are `Manual`/`WARN`, meaning kube-bench can prompt a human to
  look, but cannot itself confirm or continuously verify that this cluster's specific
  NetworkPolicy and RBAC configuration correctly isolates tenant-a from tenant-b at runtime,
  or that no tenant is currently starving another's resource share. That confirmation is
  exactly what Objectives 2–4's control-plane, network-flow, and resource monitors are built
  to provide, and what Objective 6's evaluation harness quantifies against this same static
  baseline as the comparison point.

## 6. Results of Objectives 2–6: Full Evaluation Sweep

Objectives 2–6 (control-plane monitor, network-flow monitor, resource/fairness monitor, fusion and
classification layer, CI/CD Trivy gate, scenario libraries and evaluation harness) are implemented
and were evaluated end to end on 2026-10-05. Setup: kind cluster (1 control plane, 2 workers), the
worker node pinned to a 2-CPU cpuset (`scripts/constrain-node.sh`, §6.3) so that fairness scenarios
create real contention, and k6 load against tenant-b as the independent victim-side QoS signal.

**Sweep.** All 8 built scenarios × 5 repetitions (25 isolation runs, 15 fairness runs), plus
10 negative-control windows: 5 *idle* windows and 5 *benign-busy* windows (a well-behaved tenant
saturating its own 200m CPU limit — the control that can actually separate a naive "pressure alone"
rule from the fused rule, since nothing fires on silence). Victim QoS breach ratio 1.5×.
Raw output: `evaluation/results/sweep2-ratio1.5/`.

| Detector config | Track | TP | FN | FP idle / busy | Precision | Recall | Mean latency (s) |
|---|---|---|---|---|---|---|---|
| control-plane-only | isolation | 15 | 10 | 0 / 0 | 1.00 | 0.60 | 0.22 |
| network-flow-only | isolation | 10 | 15 | 0 / 0 | 1.00 | 0.40 | 0.70 |
| **fused-isolation** | isolation | 25 | 0 | 0 / 0 | 1.00 | **1.00** | 0.41 |
| pressure-only (naive) | fairness | 15 | 0 | 0 / 3 | 0.83 | 1.00 | 13.45 |
| fused-fairness, ratio 1.5 | fairness | 13 | 2 | 0 / 5 | 0.72 | 0.87 | 2.48 |

Detection latency is injection start → first evidence of the relevant kind in the matched alert.

**Threshold sensitivity (post-hoc).** The fused fairness rule is only as discriminating as its
victim-side threshold, and 1.5× turned out too sensitive at millisecond scale: a single benign 200m
job raises victim p95 to 7–9 ms vs. 5 ms idle (1.5–1.8×), while real noisy-neighbor runs reach
10–36 ms. Replaying the sweep's k6 output through the live detector logic
(`evaluation/metrics/qos_threshold_sweep.py`; the replay reproduces the live 1.5× result):

| Breach ratio | TP | FN | FP idle / busy | Precision | Recall | Mean latency (s) |
|---|---|---|---|---|---|---|
| 1.5 | 12 | 3 | 0 / 5 | 0.71 | 0.80 | 2.49 |
| 1.75 | 15 | 0 | 0 / 5 | 0.75 | 1.00 | 2.64 |
| 2.0 | 15 | 0 | 0 / 4 | 0.79 | 1.00 | 2.77 |
| 2.5 | 15 | 0 | 0 / 0 | 1.00 | 1.00 | 4.19 |
| 3.0 | 15 | 0 | 0 / 0 | 1.00 | 1.00 | 4.73 |

Because this chooses the operating point on the data it is scored on, it was **validated on a fresh
live run** with 2.5× (new fairness runs, 5 idle + 5 benign-busy windows;
`evaluation/results/validation-ratio2.5/`), which is now the default:

| Detector config | TP | FN | FP idle / busy | Precision | Recall | Mean latency (s) |
|---|---|---|---|---|---|---|
| pressure-only (naive) | 14 | 1 | 0 / 2 | 0.88 | 0.93 | 14.54 |
| **fused-fairness, ratio 2.5** | 13 | 2 | 0 / 0 | **1.00** | 0.87 | **2.54** |

### 6.1 Interpretation

- **Isolation: the two signals are complementary, not redundant.** Control-plane-only misses every
  run of `networkpolicy-misconfig` and `dataplane-only-crossing` (no API call to observe);
  network-flow-only misses every run of `rbac-overpermission`, `credential-misuse` and
  `privilege-escalation` (no cross-tenant data-plane flow). Fusing them reaches 25/25 with no false
  positives, versus 0.60 and 0.40 for either alone; a static CIS baseline (§5) sees none of these.
- **Fairness: requiring a victim-side QoS breach buys precision and speed, at a small recall cost.**
  At the validated threshold, fused-fairness had no false positives against benign-busy tenants where
  the naive rule flagged 2–3 of 5, and detected ~6× sooner (2.5 s vs. 14.5 s; resource-pressure
  events arrive slowly because kubelet/cAdvisor metrics lag, while the k6 signal is near-real-time).
  The cost is recall (0.87 vs. 0.93 in validation): the fused rule can only fire if the victim is
  actually hurt, and 2 of 15 runs produced no qualifying evidence pair (see §6.4).
- **The result depends on one tunable.** At 1.5× the fused rule was *worse* than the naive one on
  precision (0.72 vs. 0.83). The 2.5× operating point was chosen post hoc and then confirmed on
  independent runs, but with 15 runs per condition the confidence intervals are wide.

### 6.2 Evaluation bugs found and fixed along the way

The first sweep (2026-08-31) scored `fused-fairness` at 0/15. The fusion rule was correct; the
evidence it needs never existed (see §6.3). Later analysis found further problems, all fixed
(details in `docs/risk-log.md` #15–17):
- `qos_monitor.py` was edge-triggered, emitting one event per breach onset; it now re-emits while a
  breach is sustained, and the harness waits between runs (45 s after fairness runs) so lingering
  victim latency doesn't bleed into the next run.
- A "both" alert's `first_observed` was the earliest evidence of *either* kind, so an isolation
  detection could be scored as a miss because unrelated, earlier fairness evidence had merged in
  (three isolation misses in the second sweep and one in the first). Alerts now carry per-track
  onsets (`isolation_first_observed`, `fairness_first_observed`) and scoring uses the relevant one.
- The "correlation overhead" metric measured scoring time rather than fusion cost; replaced by
  measured classifier runtime (≈0.02 s for ~1,300 events).

### 6.3 Why the testbed constrains CPU

On the unconstrained 16-core host, CFS-limited hogs did not move the victim's p95 latency at all
(≈3.6 ms before and during every injection), so there was no noisy-neighbor effect to detect.
Pinning the worker to 2 CPUs, running hogs on the victim's node, and scaling them (6×200m;
6 memory-ramping pods; a multi-process hog for `quota-bypass`) produces genuine contention. This is
engineered contention, and it is itself a finding: per-container CPU limits protected co-located
tenants well on a large host.

### 6.4 Limitations and threats to validity

- **Remaining misses.** In validation, fused-fairness missed 2 of 15 `cpu-hog-batch-job` runs: in one,
  resource-monitor never reached its 85%-of-limit pressure threshold; in the other a breach that began
  before the injection merged into the alert, so it started outside the scoring window.
- **Engineered contention, single host, single victim, one trivial workload** (`/healthz` via a
  port-forward); 5 repetitions per scenario. Results may not transfer to real multi-node clusters.
- **Post-hoc threshold selection** (validated on one fresh run, not cross-validated).
- Alerts that match no ground-truth run are reported as `unaccounted_alerts` in `metrics.json`, not
  counted as false positives; many are repeat alerts for the same event cluster, but this has not
  been audited, so precision here is measured against the negative-control windows only.
- The control-plane monitor depends on a root-owned `audit.log` workaround (`docs/risk-log.md` #9).
- Auto-remediation remains an accepted stretch goal and is not implemented.

## 7. Status

All planned components are implemented and evaluated end to end; the open items are the limitations
in §6.4.
