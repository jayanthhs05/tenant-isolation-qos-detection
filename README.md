# Unified Detection of Tenant-Isolation Violations and QoS Fairness Breaches in Multi-Tenant Cloud Platforms

Course project for IT418 (Cloud Computing) — H S Jayanth (231IT024), Suvarna G (231IT077).

Extends the base paper *"Dynamic Application Security Testing for Kubernetes Deployment: An
Experience Report from Industry"* (FSE '25) by building a working, quantitatively evaluated
system that unifies two failure modes of shared cloud infrastructure into one detection
pipeline:

- **Tenant isolation violations** (security) — Tenant A improperly accessing Tenant B's resources.
- **QoS fairness breaches** (resource management, "noisy neighbor") — Tenant A's workload
  degrading Tenant B's promised performance.

See `docs/proposal/` for the original proposal and slide deck, and `docs/architecture.md` for
the system design. The full phased implementation plan lives at
`docs/implementation-plan.md`.

## Repository layout

```
app/            Multi-tenant demo SaaS application (FastAPI), containerized
infra/          kind cluster config, Cilium/Hubble install values, per-tenant k8s manifests
static-baseline/kube-bench   Static CIS benchmark baseline (the "static-only" comparison point)
monitors/       Control-plane (audit log), network-flow (Hubble), and resource (kubelet/cAdvisor) monitors
fusion/         Correlation + classification engine unifying monitor output into isolation/fairness verdicts
scenarios/      Versioned, repeatable isolation and fairness violation injection scenarios
evaluation/     Harness that sweeps scenarios x detector configs, computes precision/recall/FPR/latency
load/           k6 load generator used as the independent victim-side QoS signal for fairness scenarios
.github/workflows/  CI/CD Trivy image-vulnerability gate
scripts/        Bootstrap/teardown convenience scripts
bin/            User-local tool installs (kubectl, kind, helm, cilium, hubble, kube-bench, trivy, k6) — gitignored
```

## Status

All phases (0–11) are implemented and evaluated end to end (8 scenarios × 5 repetitions + 10
negative controls, including benign-busy tenants). Headline results: fused isolation detection
reaches recall 1.00 (25/25) versus 0.60 (control-plane only) and 0.40 (network-flow only); fused
fairness detection has no false positives against benign-busy tenants (precision 1.00 vs. 0.88 for
the naive pressure-only rule) and detects ~6× sooner, at a small recall cost (0.87 vs. 0.93). The
fairness result depends on the victim QoS breach threshold (default 2.5×), chosen post hoc and
validated on a fresh run. Limitations are in `docs/project-report.md` §6.4 and engineering notes in
`docs/risk-log.md`. Auto-remediation is a deliberately unimplemented stretch goal. Run everything
with `scripts/full-sweep.sh` (see `COMMANDS.md`).

Where the numbers live: isolation results come from `evaluation/results/sweep2-ratio1.5/` (full
sweep at the 1.5× QoS threshold); fairness results at the default 2.5× threshold come from
`evaluation/results/validation-ratio2.5/`, a fairness-only rerun (its isolation rows are empty by
design). The top-level `evaluation/results/metrics.json` is that fairness-only run.

## Local environment

Requires Docker. All other tooling (kubectl, kind, helm, cilium CLI, hubble CLI, kube-bench,
trivy, k6) is installed as user-local binaries into `bin/` by `scripts/bootstrap-cluster.sh` —
no sudo or system package manager changes required. Add `bin/` to your `PATH` for this repo:

```sh
export PATH="$PWD/bin:$PATH"
```
