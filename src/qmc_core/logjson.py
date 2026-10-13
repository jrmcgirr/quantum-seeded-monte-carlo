"""Single-line JSON logs to stdout with redaction (HANDOFF §9.3). Never through ``logging``."""

from __future__ import annotations

SENSITIVE = ("authorization", "token", "api_key", "apikey", "secret", "block", "password")


def configure(fn: str, env: str | None = None) -> None:
    """Set the fixed fields ``fn`` (``api``/``worker``) and ``env`` (default ``$QMC_ENV``)."""
    raise NotImplementedError


def bind(**fields) -> None:
    """Set per-invocation context (``request_id``, ``job_id``, ``shard_index``, ``attempt``)."""
    raise NotImplementedError


def clear() -> None:
    """Drop the per-invocation context (call at the start of every invocation)."""
    raise NotImplementedError


def redact(value):
    """Recursively drop keys whose name contains a SENSITIVE word (case-insensitive, ``-`` read as
    ``_``) and mask string values starting with ``Bearer ``. Returns a new object."""
    raise NotImplementedError


def log(event: str, level: str = "INFO", **fields) -> dict | None:
    """Write ``{"ts", "level", "event", "fn", "env", ...context, ...fields}`` (redacted) as one line;
    levels below ``$LOG_LEVEL`` are dropped (returns None). Returns the object written."""
    raise NotImplementedError
