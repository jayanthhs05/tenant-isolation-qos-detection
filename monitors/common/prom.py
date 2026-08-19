"""Minimal Prometheus text-exposition-format parser.

Only pulls out samples for one metric name at a time — resource-monitor's only use is reading
a handful of `container_cpu_cfs_*` counters out of kubelet/cAdvisor's `/metrics/cadvisor`
endpoint, so a full client library (with histograms, exemplars, etc.) would be overkill.
"""
from __future__ import annotations

import re

_LINE_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{(?P<labels>.*)\}\s+(?P<value>\S+)")
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_metric(text: str, metric_name: str) -> list[tuple[dict[str, str], float]]:
    """Returns [(labels_dict, value), ...] for every sample of `metric_name` in `text`."""
    samples = []
    prefix = metric_name + "{"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        labels = dict(_LABEL_RE.findall(match.group("labels")))
        samples.append((labels, value))
    return samples


def parse_cpu_quantity(quantity: str | None) -> float | None:
    """Kubernetes CPU quantity ("250m", "1", "1500m") -> cores as a float. None if unset."""
    if not quantity:
        return None
    quantity = quantity.strip()
    if quantity.endswith("m"):
        return float(quantity[:-1]) / 1000.0
    return float(quantity)
