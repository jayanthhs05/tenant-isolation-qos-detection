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

## 6. Status and Next Steps

At the time of this report, Objectives 1–6 have all been implemented (control-plane monitor,
network-flow monitor, resource/fairness monitor, fusion and classification layer, CI/CD
Trivy gate, and the scenario-injection and evaluation-harness code) and smoke-tested at small
scale against live cluster events. The full quantitative evaluation sweep (all 8 built
scenarios × 5 repetitions, plus negative-control windows, scored for precision, recall,
false-positive rate, and detection latency per detector configuration — see
`docs/evaluation-methodology.md`) has not yet been executed as a complete run; that full
metrics table, and the corresponding results for Objectives 2 through 6, are reported
separately once that run is captured.
