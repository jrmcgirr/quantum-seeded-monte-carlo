"""Replay comparison (HANDOFF §5.4) and the job-level fingerprint view. No NumPy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def compare(
    original_job: Mapping,
    original_shards: Iterable[Mapping],
    replay_job: Mapping,
    replay_shards: Iterable[Mapping],
) -> dict:
    """``{"original_job_id", "bit_identical", "estimate_equal", "shards_compared", "shard_diffs":
    [{"shard_index", "field", "original_hex", "replay_hex"}], "fingerprint_match",
    "fingerprint_diffs": {"<i>": {"<key>": [orig, replay]}}}``. Byte-level compare of ``n``,
    ``sum_hex``, ``sum_sq_hex`` per shard and of the result hex; ``cpu_model`` excluded from the
    fingerprint match.
    """
    raise NotImplementedError


def job_fingerprint(shards: Iterable[Mapping]) -> dict | None:
    """The shard fingerprint when every shard agrees (ignoring ``cpu_model``), else
    ``{"mixed": True, "by_shard": [...]}``; None with no shards."""
    raise NotImplementedError
