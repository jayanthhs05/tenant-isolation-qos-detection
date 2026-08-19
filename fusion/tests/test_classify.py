#!/usr/bin/env python3
"""Fusion classification tests (Phase 8's own "done" criterion): one hand-run isolation
scenario and one hand-run fairness scenario, each correctly classified end-to-end, plus the
negative controls that make those results meaningful (elevated CPU alone isn't fairness
evidence; unrelated same-tenant/no-evidence traffic produces no alert).

Plain assert-based script rather than pytest — no test framework is used anywhere else in this
project, and these are simple enough not to need one.

Usage:
    python3 fusion/tests/test_classify.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # fusion/, for `src`
from src import classify_events  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "monitors"))  # for `common`
from common import Event  # noqa: E402


def ev(source, ts, namespace, peer_namespace=None, resource_type=None, verb=None, principal=None):
    return Event(
        source=source, timestamp=ts, namespace=namespace, principal=principal,
        peer_namespace=peer_namespace, resource_type=resource_type, verb=verb, raw={},
    )


def test_isolation_from_control_plane_alone():
    """Hand-run isolation case: tenant-a's ServiceAccount hits tenant-b via the apiserver
    (control-plane-monitor's signal). No network-flow evidence at all — rule 1 says
    control-plane-only evidence is sufficient."""
    events = [
        ev("control-plane", "2026-08-19T10:00:00+00:00", "tenant-a", "tenant-b",
           resource_type="pods", verb="get", principal="system:serviceaccount:tenant-a:app"),
    ]
    alerts = classify_events(events)
    assert len(alerts) == 1, alerts
    assert alerts[0].category == "isolation"
    assert alerts[0].tenants == ("tenant-a", "tenant-b")
    print("PASS: isolation from control-plane-only evidence")


def test_isolation_from_network_flow_alone():
    """Same, but the only evidence is a Hubble-observed cross-tenant flow (a NetworkPolicy
    misconfiguration that never touches the apiserver, so control-plane-monitor sees nothing)."""
    events = [
        ev("network-flow", "2026-08-19T10:00:05+00:00", "tenant-a", "tenant-c",
           resource_type="network-flow", verb="FORWARDED", principal="tenant-a-app"),
    ]
    alerts = classify_events(events)
    assert len(alerts) == 1, alerts
    assert alerts[0].category == "isolation"
    assert alerts[0].tenants == ("tenant-a", "tenant-c")
    print("PASS: isolation from network-flow-only evidence")


def test_pressure_alone_is_not_fairness():
    """Elevated CPU on its own, with no independent victim-side QoS breach, must not be
    classified as anything — the explicit point of rule 2."""
    events = [
        ev("resource", "2026-08-19T10:00:00+00:00", "tenant-a",
           resource_type="pod-cpu-usage", verb="pressure", principal="notes-api-a"),
        ev("resource", "2026-08-19T10:00:03+00:00", "tenant-a",
           resource_type="pod-cpu-throttle", verb="throttled", principal="notes-api-a"),
    ]
    alerts = classify_events(events)
    assert alerts == [], alerts
    print("PASS: resource pressure alone produces no alert")


def test_fairness_needs_both_pressure_and_victim_qos_breach():
    """Hand-run fairness case: tenant-a is under sustained CPU pressure, and tenant-b
    (co-located) independently shows a k6-measured QoS breach in the same window, with no
    cross-namespace access evidence anywhere -> fairness."""
    events = [
        ev("resource", "2026-08-19T10:00:00+00:00", "tenant-a",
           resource_type="pod-cpu-throttle", verb="throttled", principal="notes-api-a"),
        ev("resource", "2026-08-19T10:00:04+00:00", "tenant-b",
           resource_type="victim-qos-latency", verb="breach", principal="notes-api-b"),
    ]
    alerts = classify_events(events)
    assert len(alerts) == 1, alerts
    assert alerts[0].category == "fairness"
    assert alerts[0].tenants == ("tenant-a", "tenant-b")
    print("PASS: fairness from pressure + independent victim-side QoS breach")


def test_fairness_outside_window_is_not_correlated():
    """The same pair of signals, but far enough apart in time that they shouldn't be treated
    as related — each should surface as nothing (pressure alone is never sufficient) rather
    than being wrongly paired into a fairness alert."""
    events = [
        ev("resource", "2026-08-19T10:00:00+00:00", "tenant-a",
           resource_type="pod-cpu-throttle", verb="throttled", principal="notes-api-a"),
        ev("resource", "2026-08-19T10:05:00+00:00", "tenant-b",
           resource_type="victim-qos-latency", verb="breach", principal="notes-api-b"),
    ]
    alerts = classify_events(events)
    assert alerts == [], alerts
    print("PASS: pressure and QoS breach outside the correlation window are not paired")


def test_both_isolation_and_fairness_in_same_window_merge():
    """Rule 3: when a tenant pair has both isolation evidence and fairness evidence in the same
    window, it should be reported once as "both", not as two separate alerts."""
    events = [
        ev("control-plane", "2026-08-19T10:00:00+00:00", "tenant-a", "tenant-b",
           resource_type="pods", verb="get", principal="system:serviceaccount:tenant-a:app"),
        ev("resource", "2026-08-19T10:00:02+00:00", "tenant-a",
           resource_type="pod-cpu-throttle", verb="throttled", principal="notes-api-a"),
        ev("resource", "2026-08-19T10:00:04+00:00", "tenant-b",
           resource_type="victim-qos-latency", verb="breach", principal="notes-api-b"),
    ]
    alerts = classify_events(events)
    assert len(alerts) == 1, alerts
    assert alerts[0].category == "both", alerts
    assert alerts[0].tenants == ("tenant-a", "tenant-b")
    print("PASS: overlapping isolation + fairness evidence merges into one 'both' alert")


def test_same_namespace_traffic_is_not_flagged():
    """A tenant accessing its own namespace is not cross-tenant evidence, regardless of
    volume — negative control for isolation."""
    events = [
        ev("control-plane", "2026-08-19T10:00:00+00:00", "tenant-a", "tenant-a",
           resource_type="pods", verb="get", principal="system:serviceaccount:tenant-a:app"),
    ]
    alerts = classify_events(events)
    assert alerts == [], alerts
    print("PASS: same-namespace access produces no alert")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} fusion classification tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
