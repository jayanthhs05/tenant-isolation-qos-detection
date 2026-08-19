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

    def to_json(self) -> str:
        d: dict[str, Any] = dataclasses.asdict(self)
        d["tenants"] = list(self.tenants)
        return json.dumps(d)
