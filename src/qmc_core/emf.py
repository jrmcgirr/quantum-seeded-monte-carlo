"""CloudWatch Embedded Metric Format to stdout (HANDOFF §9.1). Never through ``logging``."""

from __future__ import annotations

import os
import time

from qmc_core.logjson import redact, write_line

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


def namespace() -> str:
    return os.environ.get("METRICS_NAMESPACE") or f"QMC/{os.environ.get('QMC_ENV', 'dev')}"


def emit(name: str, value: int | float, **properties) -> dict:
    """Write one EMF line (namespace ``$METRICS_NAMESPACE``, ``Dimensions: [[]]``, ids as
    properties) and return the object. Unknown metric names raise ValueError (≤ 10 free metrics)."""
    if name not in METRICS:
        raise ValueError(f"unknown metric {name!r}: the free tier allows 10 (HANDOFF §9.1)")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("metric value must be a number")
    clash = {"_aws", name} & properties.keys()
    if clash:
        raise ValueError(f"property names clash with the metric: {sorted(clash)}")
    obj = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace(),
                    "Dimensions": [[]],
                    "Metrics": [{"Name": name, "Unit": METRICS[name]}],
                }
            ],
        },
        name: value,
        **redact(properties),
    }
    write_line(obj)
    return obj
