# Architecture

## Goal

One fused monitoring pipeline that answers, for a multi-tenant Kubernetes cluster: *are my
tenants safely and fairly sharing this pool of infrastructure?* It reports two distinct
verdict types from shared telemetry:

- **Isolation violation** — a principal from one tenant's namespace improperly accessed a
  resource scoped to another tenant (security).
- **Fairness/QoS violation** — one tenant's workload starved another's promised resource
  share, degrading their measured performance ("noisy neighbor").

## Correction to the original proposal

The proposal (`docs/proposal/CC-Project.pdf`, §6) states that Cilium/Hubble's eBPF layer
feeds *both* detection tracks from one data collection layer. That's not literally accurate:
Hubble's eBPF probes are attached at the **network/socket layer** — excellent for
cross-namespace flow detection, useless for cgroup CPU-throttling/memory-pressure
accounting, which lives in a different subsystem entirely (kubelet/cAdvisor).

The corrected architecture below keeps the "one unified pipeline" claim (both signal sources
still feed the same fusion layer) but is explicit that the data/resource plane is really **two**
independent collectors, not one:

```
                        ┌─────────────────────┐
                        │   kube-apiserver     │
                        │   audit log (JSONL)  │
                        └──────────┬───────────┘
                                   │ tail
                                   ▼
                        ┌─────────────────────┐
                        │ control-plane-monitor│──┐
                        │ (monitors/control-   │  │
                        │  plane-monitor)       │  │
                        └─────────────────────┘  │
                                                  │
   ┌──────────────────────┐                      │
   │  Cilium eBPF agent    │  hubble observe      │
   │  (network/socket layer)├──────┐              │
   └──────────────────────┘       ▼              │
                        ┌─────────────────────┐   │      ┌────────────────────┐
                        │ network-flow-monitor │───┼─────▶│   fusion layer      │
                        │ (monitors/network-   │   │      │ (fusion/src)        │
                        │  flow-monitor)        │   │      │ correlate by        │
                        └─────────────────────┘   │      │ (namespace,peer_ns),│
                                                   │      │ principal, ~10s     │
   ┌──────────────────────┐                       │      │ window → classify   │
   │ kubelet / cAdvisor     │  poll /stats/summary  │      │ isolation / fairness│
   │ (cgroup CPU/mem layer) ├───────┐               │      │ / both              │
   └──────────────────────┘        ▼               │      └─────────┬──────────┘
                        ┌─────────────────────┐    │                │
                        │  resource-monitor     │───┘                │
                        │ (monitors/resource-   │                    │
                        │  monitor)              │                    ▼
                        └─────────────────────┘          alerts (JSONL) + evaluation/
                                   ▲
                                   │ independent victim-side QoS signal
                        ┌─────────────────────┐
                        │  k6 load generator    │
                        │  (load/k6) — rolling  │
                        │  p95 latency per tenant│
                        └─────────────────────┘
```

## Why elevated resource usage alone isn't fairness evidence

A tenant legitimately using more CPU is not a violation. The fusion layer only classifies a
**fairness** violation when resource pressure on one tenant's namespace correlates, in the same
time window, with an **independent, victim-side QoS breach** — a co-located tenant's
k6-measured rolling p95 latency crossing a threshold (default: 1.5x its established baseline).
This keeps the fairness track honest: it requires evidence of actual harm to a neighbor, not
just elevated self-usage.

## Canonical event schema (shared by all three monitors)

```json
{
  "event_id": "uuid",
  "source": "control-plane | network-flow | resource",
  "timestamp": "RFC3339, one host wall-clock across all monitors",
  "namespace": "acting tenant namespace",
  "principal": "serviceaccount / pod identity, when known",
  "peer_namespace": "namespace being accessed / flow destination, when applicable",
  "resource_type": "k8s resource kind, flow, or metric kind",
  "verb": "get/list/... | ALLOWED/DROPPED | throttled/pressure",
  "raw": { "...": "original event payload, kept for debugging" }
}
```

Network-flow events don't natively carry ServiceAccount identity (only pod/namespace/labels),
so `network-flow-monitor` enriches flows via a Kubernetes-client Pod watch that maintains a
live pod → ServiceAccount lookup table.

## Classification rules (deterministic, not ML)

Ground-truth injection (see `scenarios/`) makes rules easy to validate against known-good
labels, and fits the project's time budget better than a learned classifier.

1. Control-plane-only or network-flow-only cross-namespace evidence in the correlation
   window → **isolation**.
2. Resource-pressure evidence on tenant A **plus** an independent victim-side QoS breach on a
   co-located tenant, with **no** cross-namespace access evidence → **fairness**.
3. Both evidence types present in the same window → **both**.

## Detection latency accounting

Each monitor logs two timestamps per alert: when the raw signal was first observed, and when
the fused alert was emitted after correlation. This decomposes reported latency into
log-tail latency + rule-evaluation latency + correlation-window latency, rather than one
opaque number — see `docs/evaluation-methodology.md`.

## Threats to validity (carried into the final write-up)

- All kind "nodes" run as containers sharing one physical kernel scheduler on one host — real
  cgroup enforcement still applies, but this understates true multi-node physical isolation.
- Using a single host wall-clock across the scenario driver and all monitors is valid here
  (single machine) but would not hold on a genuine multi-node deployment with clock skew.
