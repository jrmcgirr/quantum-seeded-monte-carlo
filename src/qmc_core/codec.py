"""Exact float and set encodings for DynamoDB and JSON (HANDOFF §6.1)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping


def to_hex(x: float) -> str:
    """``float.hex()``: the only way a result float is stored or compared."""
    return float(x).hex()


def from_hex(s: str) -> float:
    """Inverse of ``to_hex`` (exact, including ±0, subnormals and inf)."""
    return float.fromhex(s)


def params_to_repr(params: Mapping[str, float | int]) -> dict[str, str]:
    """``{name: repr(float(x))}``: parameters as stored on the job item and in the SQS message."""
    return {k: repr(float(v)) for k, v in params.items()}


def params_from_repr(params: Mapping[str, str]) -> dict[str, float]:
    """Inverse of ``params_to_repr``."""
    return {k: float(v) for k, v in params.items()}


def ns_to_set(value: Iterable | None) -> frozenset[int]:
    """A DynamoDB number set as read by boto3 (set of Decimal) or None/absent → frozenset of int."""
    return frozenset(int(v) for v in value) if value else frozenset()


def iso_ms(epoch_s: float) -> str:
    """UTC ISO-8601 with milliseconds and ``Z``: ``2026-10-13T20:31:07.412Z``."""
    t = dt.datetime.fromtimestamp(epoch_s, dt.UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
