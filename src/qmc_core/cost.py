"""Per-job cost attribution (stretch, QMC-126, HANDOFF §7.10)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def estimate(job: Mapping, shards: Iterable[Mapping], polls: int = 0) -> dict | None:
    """``{"lambda_gb_seconds", "lambda_requests", "sqs_requests", "dynamodb_write_units",
    "usd_at_list_price", "usd_after_free_tier": 0.0}``; returns None until QMC-126."""
    return None
