"""Single-line JSON logs to stdout with redaction (HANDOFF §9.3). Never through ``logging``."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping

from qmc_core.codec import iso_ms

SENSITIVE = ("authorization", "token", "api_key", "apikey", "secret", "block", "password")
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
REDACTED = "[REDACTED]"

_fixed: dict = {"fn": None, "env": None}
_context: dict = {}


def configure(fn: str, env: str | None = None) -> None:
    """Set the fixed fields ``fn`` (``api``/``worker``) and ``env`` (default ``$QMC_ENV``)."""
    _fixed["fn"] = fn
    _fixed["env"] = env if env is not None else os.environ.get("QMC_ENV", "dev")


def bind(**fields) -> None:
    """Set per-invocation context (``request_id``, ``job_id``, ``shard_index``, ``attempt``)."""
    _context.update(fields)


def clear() -> None:
    """Drop the per-invocation context (call at the start of every invocation)."""
    _context.clear()


def _sensitive(key: object) -> bool:
    k = str(key).lower().replace("-", "_")
    return any(word in k for word in SENSITIVE)


def redact(value):
    """Recursively drop keys whose name contains a SENSITIVE word (case-insensitive, ``-`` read as
    ``_``) and mask string values starting with ``Bearer ``. Returns a new object."""
    if isinstance(value, Mapping):
        return {k: redact(v) for k, v in value.items() if not _sensitive(k)}
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [redact(v) for v in items]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return REDACTED
    return value


def _default(o):
    if hasattr(o, "as_integer_ratio") and not isinstance(o, float):  # Decimal from boto3
        return int(o) if o == int(o) else str(o)
    return str(o)


def write_line(obj: Mapping) -> None:
    """One compact JSON object per line on stdout, flushed (shared with ``emf``)."""
    sys.stdout.write(json.dumps(obj, separators=(",", ":"), default=_default) + "\n")
    sys.stdout.flush()


def log(event: str, level: str = "INFO", **fields) -> dict | None:
    """Write ``{"ts", "level", "event", "fn", "env", ...context, ...fields}`` (redacted) as one line;
    levels below ``$LOG_LEVEL`` are dropped (returns None). Returns the object written."""
    level = level.upper()
    threshold = LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").upper(), 20)
    if LEVELS.get(level, 20) < threshold:
        return None
    obj = redact(
        {"ts": iso_ms(time.time()), "level": level, "event": event, **_fixed, **_context, **fields}
    )
    write_line(obj)
    return obj
