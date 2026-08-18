# Risk Log

Living document — update as risks are resolved or new ones surface. Each entry: status,
description, mitigation.

## 1. Hubble-for-resource-metrics gap
**Status:** Resolved by design (see `docs/architecture.md`).
Hubble is network/socket-layer only; it cannot see cgroup CPU-throttling/memory-pressure.
Architecture treats the data/resource plane as two independent collectors (network-flow via
Hubble, resource via kubelet/cAdvisor) both feeding one fusion layer.

## 2. kube-bench on kind is fiddly
**Status:** Open — expect in Phase 4.
kind's control-plane node is a container, not a standard node, so kube-bench's default
master-node checks (expecting specific hostPath-mounted config files) often misfire. Budget
explicit debugging time. Also: kube-bench does not check for missing/misconfigured
ResourceQuotas at all — a small supplementary check script is needed to make the proposal's
"basic resource-quota misconfiguration" static-baseline claim literally true.

## 3. False-positive rate is ill-posed without negative controls
**Status:** Open — must be designed into `evaluation/harness` from the start (Phase 11).
A continuous event stream has no natural "true negative." The evaluation harness must
explicitly sample idle/no-injection windows as negative-opportunity periods, or FPR becomes
hand-wavy.

## 4. Cilium + kind version compatibility
**Status:** Open — highest-risk single setup step, addressed in Phase 1.
Pin specific tested versions of the kind node image and the Cilium Helm chart rather than
"latest" of both. Budget a dedicated session purely for "get Cilium healthy on kind" before
building anything on top of it. Verify with an explicit eBPF smoke test (native Linux host
here, so risk is lower than Docker Desktop on Mac/Windows, but still verify, don't assume).

## 5. Single-host resource contention is somewhat artificial
**Status:** Open — mitigate operationally, document as threat-to-validity in Phase 12.
All kind "nodes" share one physical kernel scheduler on one laptop. Multi-node config buys
RBAC/namespace realism more than genuine physical resource isolation. Avoid running other
heavy processes on the host during evaluation runs to reduce measurement noise.

## 6. Evaluation scope is large for a two-person team in one semester
**Status:** Open — mitigation: explicit minimum-viable evaluation scope (Phase 11).
5 isolation scenario types x 3-4 fairness scenario types x 3 detector configs x repetitions,
with full precision/recall/FPR/latency breakdowns per track, is a lot. Guarantee a defensible
minimum (e.g. 3 isolation + 2 fairness scenario types x 5 repetitions) exists early, treat the
rest as stretch.

## 7. Auto-remediation is out of core scope
**Status:** Accepted as strict stretch goal.
Re-applying resource quotas automatically is an automated *write* action against the live
cluster — a materially different risk profile than passive detection, and not required to
satisfy the proposal's core claims. Do not build it into the critical path.

## 8. eBPF privilege requirements
**Status:** Low risk on this host, verify anyway.
Native Linux host (Arch, modern kernel) with Docker — this is the environment where Cilium's
eBPF requirements are least likely to cause trouble (vs. Docker Desktop on Mac/Windows). Still
run an explicit smoke test (`hubble observe` on live traffic) in Phase 1 rather than assuming
it "just works."
