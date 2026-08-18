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
