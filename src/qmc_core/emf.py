"""CloudWatch Embedded Metric Format to stdout (HANDOFF §9.1). Never through ``logging``."""

from __future__ import annotations

METRICS: dict[str, str] = {
    "JobsSubmitted": "Count",
    "EntropyKiB": "Kilobytes",
    "PathsSimulated": "Count",
    "ShardFailures": "Count",
    "JobsCompleted": "Count",
    "JobsPartial": "Count",
    "SeedingFailures": "Count",
    "QryptLatencyMs": "Milliseconds",
}


def emit(name: str, value: int | float, **properties) -> dict:
    """Write one EMF line (namespace ``$METRICS_NAMESPACE``, ``Dimensions: [[]]``, ids as
    properties) and return the object. Unknown metric names raise ValueError (≤ 10 free metrics)."""
    raise NotImplementedError
