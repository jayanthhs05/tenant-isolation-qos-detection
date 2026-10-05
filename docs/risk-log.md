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

## 9. audit.log is root-owned 600 on the host, unreadable by the monitor's user
**Status:** Resolved (workaround), monitor from a monitor-lifecycle risk remains.
The apiserver static pod writes `audit.log` as root inside the control-plane container; on
the host bind mount that's `root:root` mode 600, even though the file lives under our own
project directory — the invoking user cannot read it directly, and this project deliberately
avoids sudo/system changes. Fix: `docker exec <control-plane-container> chmod 644
/etc/kubernetes/audit/audit.log` — root chmod'ing its own file from inside the container,
which changes host-side permissions too since it's the same inode. Baked into
`scripts/bootstrap-cluster.sh` (runs automatically after cluster creation, with a
wait-for-file-to-exist retry loop). Residual risk: kube-apiserver's built-in log rotation
(`audit-log-maxsize`/`-maxbackup`) could create a fresh root:600 file on rotation during a
long-running evaluation session — `monitors/control-plane-monitor` should tolerate a read
failure by re-attempting (and re-chmod via the same docker-exec trick) rather than crashing,
or the team can just re-run the chmod step before long evaluation runs (Phase 11).

## 10. Cilium MTU red herring (documented so it isn't re-chased)
**Status:** Ruled out, kept for the record.
Initial `hubble-relay` failures (`context deadline exceeded` syncing with `hubble-peer`,
consistently at ~30s) looked exactly like a classic Cilium-on-VXLAN MTU bug: Cilium had
auto-detected MTU 1500 for `cilium_vxlan`/`cilium_host` — identical to the underlying
kind/docker bridge MTU — which is wrong once ~50 bytes of VXLAN encapsulation overhead are
added, and normally causes larger packets (like a TLS certificate exchange) to black-hole
while small packets (like a bare TCP handshake) go through fine, which is exactly the pattern
observed. Setting `MTU: 1450` explicitly in `infra/cilium/values.yaml` and restarting the
Cilium DaemonSet did not fix it, and a direct `curl` mTLS handshake against both the agent pod
IP and the `hubble-peer` Service (from the same node hosting `hubble-relay`, using its real
extracted client cert) completed in under 30ms in every configuration — ruling out MTU,
routing, and cert/CA mismatch entirely. `MTU: 1450` is still correct to keep (it's the right
value for VXLAN-over-1500 regardless), but it was not the actual bug. See #11 for the real
root cause.

## 11. Host DNS search domain leaks into every pod and gets hijacked
**Status:** Resolved.
The actual cause of #10's symptom: Docker Engine copies the *host's* `/etc/resolv.conf`
`search` line into each kind node container's own resolv.conf (confirmed via `docker exec
tenant-lab-control-plane cat /etc/resolv.conf`, which showed `search nitk.ac.in` — the
college network's own search domain from this machine's NetworkManager config). Kubelet then
propagates that node-level search domain into every pod's resolv.conf alongside the normal
cluster ones. `nitk.ac.in` answers essentially any subdomain with a wildcard record pointing
at `127.0.0.1`/`::1` (confirmed directly: `hubble-peer.kube-system.svc.cluster.local.nitk.ac.in`
resolves to `::1`/`127.0.0.1` through the cluster's own CoreDNS). Because in-cluster names
like `hubble-peer.kube-system.svc.cluster.local` have fewer dots (4) than the default
`ndots:5`, resolvers try search-suffixed candidates — including the `nitk.ac.in`-suffixed one
— before the bare absolute name, so a external wildcard answer can substitute for the real
cluster-local one and route Go's gRPC client to the pod's own loopback instead of the actual
Hubble peer. **Fix:** `infra/kind/resolv-clean.conf` (tracked, no `search` line, same
Docker-embedded nameserver IP) is bind-mounted into every node and set as kubelet's
`resolvConf` via a `KubeletConfiguration` `kubeadmConfigPatches` entry in
`infra/kind/kind-config.yaml.tmpl`, so no node ever hands pods the host's search domain in
the first place. This is cluster-wide and requires no per-pod changes. **Portability note:**
the nameserver IP in `resolv-clean.conf` (`172.18.0.1`) is this machine's current Docker
embedded-DNS address for the `kind` network — verify it still matches (`docker exec
tenant-lab-control-plane cat /etc/resolv.conf` on a fresh, pre-fix node) before reusing this
config on a different machine or after a Docker upgrade.

## 12. `kubectl ... --token=X` does not reliably impersonate a tenant identity
**Status:** Resolved — use raw `curl` (or a dedicated kubeconfig context) for identity-scoped
scenario scripts instead.
Hit while manually verifying Phase 5's control-plane monitor: `kubectl get pods -n tenant-b
--token="$(kubectl create token tenant-a-app -n tenant-a)"` returned a real pod list — looking
exactly like an RBAC bypass. It wasn't one. Checking the audit log for that exact request
showed it was authenticated as `kubernetes-admin` (the ambient kubeconfig's existing
client-certificate identity), not `tenant-a-app` — `--token` did not cleanly override the
kubeconfig's already-configured client-cert auth for this context. A raw `curl -H
"Authorization: Bearer $TOKEN" <apiserver>/api/v1/namespaces/tenant-b/pods` with the same
token correctly returned `403 Forbidden`, confirming RBAC itself was never broken. **Takeaway
for scenarios/ (Phase 10) and anyone re-testing RBAC manually:** don't trust `kubectl
--token=X` layered on top of an existing admin kubeconfig context to prove or disprove an
isolation boundary — verify with `curl` against the apiserver directly (or a kubeconfig
context that has no other auth configured), and cross-check against what the audit log
actually recorded as `user.username` for that request.

## 13. Hubble doesn't resolve peer identity on egress-side policy drops
**Status:** Resolved — network-flow-monitor handles this explicitly.
Hit while validating Phase 6's network-flow monitor: a cross-tenant request blocked by a
tenant's own default-deny **egress** NetworkPolicy produces a `DROPPED` /
`drop_reason_desc: POLICY_DENIED` flow where `source.namespace` is populated but
`destination.namespace` is **absent** — confirmed live (`hubble observe --verdict DROPPED -o
json`) — because Cilium drops the packet before it ever reaches the peer's endpoint, so the
peer's Cilium identity (and therefore its namespace) is never resolved; only the destination
IP and a fallback identity (`2`, "world") are known. A naive classifier requiring both
namespaces to be known and different would silently miss every egress-side deny — the far
more common enforcement path, since NetworkPolicy's default-deny-egress is what most tenant
namespaces here actually rely on. **Fix:** `classify()` in
`monitors/network-flow-monitor/monitor.py` has a second rule: `verdict == "DROPPED"` +
`drop_reason_desc == "POLICY_DENIED"` + known tenant `source.namespace` + unresolved
`destination.namespace` still emits an event, with `peer_namespace: null` (never fabricated)
and `resource_type: "network-flow-egress-denied"` so it's distinguishable downstream from a
fully-resolved cross-tenant flow. Ingress-side denies (evaluated at the destination endpoint,
which by definition has already resolved its own identity) are unaffected and get full
`peer_namespace` resolution through the normal path.

## 14. NetworkPolicy on a Service ClusterIP is a no-op with kubeProxyReplacement disabled
**Status:** Resolved — scenarios target pod IPs directly, not Service ClusterIPs.
Hit while building Phase 10's `isolation/01-networkpolicy-misconfig` scenario: broadening
*both* tenant-a's egress and tenant-b's ingress selectors (confirmed applied via `kubectl get
networkpolicy -o jsonpath`) still left a cross-tenant request to tenant-b's Service ClusterIP
timing out, while an identical request straight to the same backend pod's own IP succeeded
immediately. Root cause: `infra/cilium/values.yaml` sets `kubeProxyReplacement: false`, so
kube-proxy (not Cilium) owns Service NAT via iptables. Cilium's eBPF policy hook sits at the
pod's veth, *upstream* of that NAT — it evaluates the packet against the still-unresolved
ClusterIP, which matches no Cilium-managed endpoint identity, and drops it regardless of how
permissive the NetworkPolicy's namespaceSelector rules are; those rules only ever match real
pod/namespace identities, never a bare Service IP. **Fix:** every scenario that needs to
demonstrate an *allowed* cross-tenant flow (`scenarios/isolation/01-networkpolicy-misconfig`,
`05-dataplane-only-crossing`) targets the peer pod's own IP directly. **Implication beyond
scenarios:** this cluster configuration means NetworkPolicy alone cannot govern Service-level
traffic — namespaceSelector-based policies only take effect for traffic that already resolves
to a real pod identity (direct pod IP, or traffic Cilium itself NAT's under
`kubeProxyReplacement: true`). Worth flagging explicitly in the Phase 12 write-up as a
configuration-specific caveat, not a general NetworkPolicy limitation.

## 15. Fairness scenarios never degraded the victim, so fused fairness detection scored 0
**Status:** Resolved.
The first full sweep scored `fused-fairness` at 0/15 recall while the `pressure-only` ablation
got 7/15. The fusion rule was correct; its required evidence never existed. Replaying the k6
output showed the victim's (tenant-b) p95 latency was ~3.6 ms both before and during every
fairness injection, so `qos_monitor.py` never emitted a `victim-qos-latency` breach. Cause: the
host has 16 cores and the hogs were CFS-limited to 200m–1.5 cores, so they could not contend
with a trivial `/healthz` handler. Four fixes, none of which touch the fusion rule itself:
1. `scripts/constrain-node.sh` (called from `up.sh`) pins the kind worker node to a 2-CPU
   cpuset, so a handful of CFS-limited hog pods can actually saturate it.
2. `scenarios/fairness/lib.sh`: hogs are scheduled on the node running the victim's pod (before
   this they landed there only by scheduler luck) and carry an `app=noisy-hog` label for revert.
   Scenario 01 runs 6×200m hogs, 02 runs 6 memory-ramping pods that keep rewriting their working
   set, 03 runs a multi-process hog (a single-threaded loop can never reach 85% of a 1500m
   limit, so resource-monitor emitted no pressure events for it).
3. `qos_monitor.py` was edge-triggered (one event per breach onset). With contention lingering
   across back-to-back runs it emitted a single event for the whole sweep. It now re-emits every
   `--reemit-seconds` (5) while the breach is sustained, and the harness adds a
   `--cooldown-seconds` (20) gap after each scenario run so victim latency recovers.
4. Validation: victim p95 went from ~5.7 ms baseline to 17–95 ms during injections (≥3×, over
   the 1.5× breach ratio).
Caveat for the write-up: this is deliberate contention engineering. On an unconstrained
16-core host these hogs are not a real noisy-neighbor problem, which is itself a finding about
CFS limits protecting co-located tenants.

## 16. Evaluation tooling bugs found while closing #15
**Status:** Resolved.
- `mean_correlation_overhead_seconds` was `Alert.fused_at - first_observed`, but `fused_at` is
  stamped when the batch is scored, so it measured "how long ago the event happened" (~330 s),
  not fusion overhead. Replaced by `classification_runtime_seconds` (measured) in
  `compute_metrics.py`.
- `scripts/install-tools.sh` version checks used `cmd | grep -q`; `grep -q` exits on first match,
  the upstream command gets SIGPIPE, and under `pipefail` the check intermittently reported
  "not installed", triggering a re-download that could abort `scripts/up.sh`. Checks now read
  the whole stream, and a failed download keeps an existing binary.

## 17. Scoring and sensitivity issues found in the second sweep
**Status:** Resolved (17a, 17b); Open / documented (17c).
- **17a. "both" alerts hid isolation detections.** `Alert.first_observed` is the earliest evidence of
  either kind, so an isolation alert merged with lingering fairness evidence from a preceding run
  started before its injection window and scored as a miss (3 of 25 isolation runs). Fix: per-track
  onsets on `Alert` (`isolation_first_observed`, `fairness_first_observed`); `compute_metrics.py`
  scores each track by its own evidence. Longer cooldown after fairness runs (45 s) reduces the
  merging in the first place.
- **17b. The 1.5× QoS breach ratio was too sensitive.** A single benign 200m job raised victim p95
  to 7–9 ms vs. 5 ms idle, so the fused rule false-alarmed on all 5 benign-busy windows (worse than
  naive pressure-only on precision). `qos_threshold_sweep.py` replays k6 output at several ratios;
  2.5× removes the false positives, and a fresh live run at 2.5× confirmed it (precision 1.00,
  recall 0.87). 2.5× is now the default in `qos_monitor.py` and `run-fairness-load.sh`.
- **17c. Remaining recall gaps** (fused-fairness 13/15): a run where resource-monitor never crossed
  its 85% pressure threshold, and one where a pre-existing breach merged into the alert.
  Not fixed; documented in the report.
