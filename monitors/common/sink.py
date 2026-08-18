"""Append-only JSON Lines event sink, shared by all monitors.

JSONL was chosen (docs/implementation-plan.md "Tech Stack") because it's append-only,
greppable, and loads straight into pandas for the evaluation scoring scripts — no shared
database or message broker needed at this scale.
"""
from __future__ import annotations

from pathlib import Path

from .event import Event


class JsonlSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", buffering=1)  # line-buffered: readable while tailing

    def write(self, event: Event) -> None:
        self._fh.write(event.to_json() + "\n")

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
