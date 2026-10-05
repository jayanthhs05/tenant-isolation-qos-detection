"""Fused alert produced by the classification engine (classify.py)."""
from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any, Optional


@dataclasses.dataclass
class Alert:
    category: str  # "isolation" | "fairness" | "both"
    tenants: tuple[str, Optional[str]]  # sorted (tenant_a, tenant_b) pair
    sources: list[str]  # contributing monitor `source` values, e.g. ["control-plane", "network-flow"]
    evidence_event_ids: list[str]
    first_observed: str  # earliest contributing raw-signal timestamp — see docs/architecture.md
    fused_at: str  # when this alert was emitted, after correlation
    detection_latency_seconds: float
    alert_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    # Per-track evidence onset. A "both" alert's `first_observed` is the earliest evidence of
    # either kind, so judging an isolation (or fairness) detection by it would be thrown off by
    # unrelated earlier evidence of the other kind that happened to merge in. None if the alert
    # has no evidence of that kind.
    isolation_first_observed: Optional[str] = None  # earliest control-plane / network-flow event
    fairness_first_observed: Optional[str] = None  # earliest resource event

    def to_json(self) -> str:
        d: dict[str, Any] = dataclasses.asdict(self)
        d["tenants"] = list(self.tenants)
        return json.dumps(d)
