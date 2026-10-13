"""Replay comparison (HANDOFF §5.4) and the job-level fingerprint view. No NumPy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

SHARD_FIELDS = ("n", "sum_hex", "sum_sq_hex")
RESULT_FIELDS = ("estimate_hex", "std_error_hex", "ci95_hex", "n")
INFORMATIONAL = frozenset({"cpu_model"})  # reported, never part of fingerprint_match


def _by_index(shards: Iterable[Mapping]) -> dict[int, Mapping]:
    return {int(s["shard_index"]): s for s in shards}


def _plain(v):
    """Decimal → int so values compare and serialize the same on both sides."""
    if isinstance(v, list | tuple):
        return [_plain(x) for x in v]
    if v is not None and not isinstance(v, str | bool | int) and hasattr(v, "as_integer_ratio"):
        return int(v)
    return v


def _matchable(fp: Mapping | None) -> dict:
    return {k: _plain(v) for k, v in (fp or {}).items() if k not in INFORMATIONAL}


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
    orig, rep = _by_index(original_shards), _by_index(replay_shards)
    shard_diffs: list[dict] = []
    fp_diffs: dict[str, dict] = {}
    for i in sorted(orig.keys() | rep.keys()):
        a, b = orig.get(i), rep.get(i)
        if a is None or b is None:
            shard_diffs.append(
                {
                    "shard_index": i,
                    "field": "missing",
                    "original_hex": "present" if a is not None else None,
                    "replay_hex": "present" if b is not None else None,
                }
            )
            continue
        for f in SHARD_FIELDS:
            va, vb = _plain(a.get(f)), _plain(b.get(f))
            if va != vb:
                shard_diffs.append(
                    {"shard_index": i, "field": f, "original_hex": va, "replay_hex": vb}
                )
        fa, fb = _matchable(a.get("fingerprint")), _matchable(b.get("fingerprint"))
        diff = {
            k: [fa.get(k), fb.get(k)]
            for k in sorted(fa.keys() | fb.keys())
            if fa.get(k) != fb.get(k)
        }
        if diff:
            fp_diffs[str(i)] = diff

    ra, rb = original_job.get("result"), replay_job.get("result")
    estimate_equal = bool(ra and rb and ra.get("estimate_hex") == rb.get("estimate_hex"))
    results_equal = bool(
        ra and rb and all(_plain(ra.get(f)) == _plain(rb.get(f)) for f in RESULT_FIELDS)
    )
    return {
        "original_job_id": original_job.get("job_id"),
        "bit_identical": not shard_diffs and results_equal,
        "estimate_equal": estimate_equal,
        "shards_compared": len(orig.keys() & rep.keys()),
        "shard_diffs": shard_diffs,
        "fingerprint_match": not fp_diffs,
        "fingerprint_diffs": fp_diffs,
    }


def job_fingerprint(shards: Iterable[Mapping]) -> dict | None:
    """The shard fingerprint when every shard agrees (ignoring ``cpu_model``), else
    ``{"mixed": True, "by_shard": [...]}``; None with no shards."""
    rows = sorted(shards, key=lambda s: int(s["shard_index"]))
    if not rows:
        return None
    fps = [_matchable(s.get("fingerprint")) for s in rows]
    if all(fp == fps[0] for fp in fps):
        return fps[0]
    return {
        "mixed": True,
        "by_shard": [
            {
                "shard_index": int(s["shard_index"]),
                **{k: _plain(v) for k, v in (s.get("fingerprint") or {}).items()},
            }
            for s in rows
        ],
    }
