"""Correlation + classification engine (Phase 8).

Consumes the canonical Events emitted by control-plane-monitor, network-flow-monitor, and
resource-monitor and turns them into fused Alerts, following the deterministic rule table in
docs/architecture.md:

1. Control-plane-only or network-flow-only cross-namespace evidence in the correlation
   window -> isolation.
2. Resource-pressure evidence on tenant A plus an independent victim-side QoS breach on a
   co-located tenant, with no cross-namespace access evidence -> fairness.
3. Both evidence types present in the same window -> both.

Deliberately rule-based rather than learned: ground-truth injection (scenarios/) makes a rule
table easy to validate end-to-end, and fits the project's time budget far better than a
classifier that itself would need its own training/evaluation split.

Batch-oriented (classify_events takes a fixed list and returns a fixed list of alerts) rather
than a live streaming service — this is the shape the evaluation harness (Phase 11) actually
needs: run a scenario, then score the resulting event logs against ground truth. run_fusion.py
can simply be re-run against a growing set of monitor log files during a live session; nothing
here depends on being invoked exactly once.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "monitors"))  # for `common`
from common import Event, now_iso  # noqa: E402

from .alert import Alert  # noqa: E402

CORRELATION_WINDOW_SECONDS = 10.0

# resource-monitor's own resource_type values (monitors/resource-monitor/monitor.py)
PRESSURE_RESOURCE_TYPES = {"pod-cpu-usage", "pod-cpu-throttle", "pod-memory-usage"}
# not yet emitted by any monitor as of Phase 8 (needs the Phase-10+ k6 load generator to
# exist) — defined here so the classification rule and its tests are ready the moment
# load/k6 starts producing this event shape: source="resource",
# resource_type="victim-qos-latency", namespace=<victim tenant>, verb="breach".
QOS_BREACH_RESOURCE_TYPE = "victim-qos-latency"


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _cluster_by_gap(events: list[Event], window_seconds: float) -> list[list[Event]]:
    """Greedily groups a time-sorted list of events into clusters where consecutive events are
    no more than `window_seconds` apart. This is the "~10s sliding window" from
    docs/architecture.md, applied per evidence group (e.g. per tenant-pair, per tenant)."""
    if not events:
        return []
    ordered = sorted(events, key=lambda e: _parse_ts(e.timestamp))
    clusters: list[list[Event]] = [[ordered[0]]]
    for event in ordered[1:]:
        gap = (_parse_ts(event.timestamp) - _parse_ts(clusters[-1][-1].timestamp)).total_seconds()
        if gap <= window_seconds:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    return clusters


def _cluster_range(cluster: list[Event]) -> tuple[datetime, datetime]:
    timestamps = [_parse_ts(e.timestamp) for e in cluster]
    return min(timestamps), max(timestamps)


def _ranges_overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime], slack_seconds: float) -> bool:
    a_start, a_end = a
    b_start, b_end = b
    slack = timedelta(seconds=slack_seconds)
    return a_start - slack <= b_end and b_start - slack <= a_end


def _pair_to_tenants(pair: frozenset) -> tuple[str, str | None]:
    """frozenset of 1 or 2 tenant names -> a sorted (tenant, tenant-or-None) tuple. A
    single-element pair shows up for the pressure-only fairness ablation (evaluation/), where
    a noisy tenant is flagged with no specific victim identified."""
    ordered = sorted(pair)
    return (ordered[0], ordered[1] if len(ordered) > 1 else None)


def _make_alert(category: str, tenants: tuple[str, str | None], clusters: list[list[Event]]) -> Alert:
    all_events = [e for cluster in clusters for e in cluster]
    first_observed = min(_parse_ts(e.timestamp) for e in all_events)
    return Alert(
        category=category,
        tenants=tuple(sorted(t for t in tenants if t is not None)) if tenants[1] else (tenants[0], None),
        sources=sorted({e.source for e in all_events}),
        evidence_event_ids=[e.event_id for e in all_events],
        first_observed=first_observed.isoformat(),
        fused_at=now_iso(),
        detection_latency_seconds=(datetime.now(timezone.utc) - first_observed).total_seconds(),
    )


def classify_events(
    events: list[Event],
    window_seconds: float = CORRELATION_WINDOW_SECONDS,
    require_qos_breach: bool = True,
) -> list[Alert]:
    """`require_qos_breach=False` is the evaluation harness's "pressure-only" detector-config
    ablation (evaluation/): a naive detector that treats elevated CPU/memory alone as
    sufficient fairness evidence, without correlating it against an independent victim-side
    QoS breach. It exists to be *compared against* the real rule below (require_qos_breach=True,
    the default and the only mode any monitor/CLI in this project actually uses), quantifying
    why that correlation requirement exists rather than just asserting it in prose."""
    isolation_events = [
        e for e in events
        if e.source in ("control-plane", "network-flow")
        and e.namespace and e.peer_namespace and e.namespace != e.peer_namespace
    ]
    pressure_events = [e for e in events if e.source == "resource" and e.resource_type in PRESSURE_RESOURCE_TYPES]
    qos_events = [e for e in events if e.source == "resource" and e.resource_type == QOS_BREACH_RESOURCE_TYPE]

    # --- isolation: cluster per unordered tenant-pair ---
    isolation_by_pair: dict[frozenset, list[Event]] = {}
    for event in isolation_events:
        isolation_by_pair.setdefault(frozenset({event.namespace, event.peer_namespace}), []).append(event)

    # each entry: (unordered tenant pair, time range, contributing event clusters)
    isolation_alerts: list[tuple[frozenset, tuple[datetime, datetime], list[list[Event]]]] = []
    for pair, group in isolation_by_pair.items():
        for cluster in _cluster_by_gap(group, window_seconds):
            isolation_alerts.append((pair, _cluster_range(cluster), [cluster]))

    # --- fairness: cluster pressure and QoS-breach evidence per tenant, then pair any two
    # different tenants whose clusters overlap in time (single-host contention means we can't
    # attribute causality more precisely than "overlapping in time" — see risk-log #5) ---
    pressure_by_tenant: dict[str, list[list[Event]]] = {}
    for event in pressure_events:
        pressure_by_tenant.setdefault(event.namespace, []).append(event)
    pressure_clusters = {
        tenant: _cluster_by_gap(group, window_seconds) for tenant, group in pressure_by_tenant.items()
    }

    qos_by_tenant: dict[str, list[Event]] = {}
    for event in qos_events:
        qos_by_tenant.setdefault(event.namespace, []).append(event)
    qos_clusters = {tenant: _cluster_by_gap(group, window_seconds) for tenant, group in qos_by_tenant.items()}

    # each entry: (unordered tenant pair, time range, contributing event clusters)
    fairness_alerts: list[tuple[frozenset, tuple[datetime, datetime], list[list[Event]]]] = []
    if require_qos_breach:
        for noisy_tenant, p_clusters in pressure_clusters.items():
            for victim_tenant, v_clusters in qos_clusters.items():
                if victim_tenant == noisy_tenant:
                    continue
                for p_cluster in p_clusters:
                    p_range = _cluster_range(p_cluster)
                    for v_cluster in v_clusters:
                        v_range = _cluster_range(v_cluster)
                        if _ranges_overlap(p_range, v_range, window_seconds):
                            pair = frozenset({noisy_tenant, victim_tenant})
                            combined_range = (min(p_range[0], v_range[0]), max(p_range[1], v_range[1]))
                            fairness_alerts.append((pair, combined_range, [p_cluster, v_cluster]))
    else:
        # Ablation baseline: pressure alone is "sufficient", no victim pairing at all —
        # single-element pair, no peer tenant identified.
        for noisy_tenant, p_clusters in pressure_clusters.items():
            for p_cluster in p_clusters:
                fairness_alerts.append((frozenset({noisy_tenant}), _cluster_range(p_cluster), [p_cluster]))

    # --- rule 3: merge an isolation alert and a fairness alert into "both" when they share a
    # tenant pair and overlap in time; elevated CPU alone (a fairness alert with no matching
    # isolation evidence) or a boundary crossing alone (isolation with no fairness evidence)
    # each stay as their own category ---
    merged: list[Alert] = []
    used_isolation: set[int] = set()
    used_fairness: set[int] = set()
    for i, (i_pair, i_range, i_clusters) in enumerate(isolation_alerts):
        for j, (f_pair, f_range, f_clusters) in enumerate(fairness_alerts):
            if j in used_fairness:
                continue
            if i_pair == f_pair and _ranges_overlap(i_range, f_range, window_seconds):
                merged.append(_make_alert("both", _pair_to_tenants(i_pair), i_clusters + f_clusters))
                used_isolation.add(i)
                used_fairness.add(j)
                break

    for i, (pair, _, clusters) in enumerate(isolation_alerts):
        if i not in used_isolation:
            merged.append(_make_alert("isolation", _pair_to_tenants(pair), clusters))
    for j, (pair, _, clusters) in enumerate(fairness_alerts):
        if j not in used_fairness:
            merged.append(_make_alert("fairness", _pair_to_tenants(pair), clusters))

    return merged
