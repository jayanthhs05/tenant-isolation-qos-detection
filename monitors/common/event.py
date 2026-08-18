"""Canonical event schema shared by all three monitors and the fusion layer.

See docs/architecture.md "Canonical event schema" — this is the single Python source of truth
for that schema, so control-plane-monitor, network-flow-monitor, resource-monitor, and
fusion/src all parse/produce the exact same shape instead of drifting apart.
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def now_iso() -> str:
    """RFC3339 timestamp using this host's wall clock — see docs/architecture.md's note on
    using one host wall-clock across the scenario driver and all monitors."""
    return datetime.now(timezone.utc).isoformat()


@dataclasses.dataclass
class Event:
    source: str  # "control-plane" | "network-flow" | "resource"
    timestamp: str  # RFC3339, when this monitor first observed the raw signal
    namespace: Optional[str]  # acting tenant namespace
    principal: Optional[str]  # serviceaccount / pod identity, when known
    peer_namespace: Optional[str]  # namespace being accessed / flow destination, when applicable
    resource_type: Optional[str]  # k8s resource kind, flow, or metric kind
    verb: Optional[str]  # get/list/... | ALLOWED/DROPPED | throttled/pressure
    raw: dict[str, Any]  # original event payload, kept for debugging
    event_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, line: str) -> "Event":
        return cls(**json.loads(line))
