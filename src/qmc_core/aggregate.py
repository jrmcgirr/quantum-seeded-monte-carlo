"""Shard aggregation and the finalize decision (HANDOFF §7.4, §7.7).

``aggregate`` is the only place a job result is computed; ``decide`` is the only place the
COMPLETE/PARTIAL decision is made. The worker and the API's lazy finalize both call them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from qmc_core.codec import from_hex, ns_to_set, to_hex

Z95 = 1.959963984540054


@dataclass(frozen=True)
class ShardResult:
    """One shard's sums (``sim.simulate_shard`` returns it; ``ddb.shard_put`` stores it as hex)."""

    n: int  # paths (pairs for antithetic)
    sum: float
    sum_sq: float


@dataclass(frozen=True)
class Result:
    n: int
    estimate: float
    std_error: float | None  # None when n < 2
    ci95: tuple[float, float] | None  # None when n < 2

    def to_item(self) -> dict:
        """DynamoDB map: ``estimate_hex, std_error_hex, ci95_hex, n`` (hex strings or None; no floats)."""
        return {
            "n": self.n,
            "estimate_hex": to_hex(self.estimate),
            "std_error_hex": None if self.std_error is None else to_hex(self.std_error),
            "ci95_hex": None if self.ci95 is None else [to_hex(c) for c in self.ci95],
        }

    def to_view(self) -> dict:
        """API view (§5.4): floats plus their hex strings."""
        return {
            "estimate": self.estimate,
            "std_error": self.std_error,
            "ci95": None if self.ci95 is None else list(self.ci95),
            "n": self.n,
            **{k: v for k, v in self.to_item().items() if k != "n"},
        }

    @classmethod
    def from_item(cls, item: Mapping) -> Result:
        """Inverse of ``to_item`` (accepts boto3's Decimal for ``n``)."""
        se = item.get("std_error_hex")
        ci = item.get("ci95_hex")
        return cls(
            n=int(item["n"]),
            estimate=from_hex(item["estimate_hex"]),
            std_error=None if se is None else from_hex(se),
            ci95=None if ci is None else (from_hex(ci[0]), from_hex(ci[1])),
        )


@dataclass(frozen=True)
class Decision:
    """What to do with a job item.

    ``action``: ``"none"`` (not terminal yet, or already finalized), ``"complete"`` or
    ``"partial"``. For ``"partial"``, aggregate over ``done`` only, pass ``add_failed`` to
    ``ddb.finalize_partial`` (empty → no ADD) and ``n_done_seen`` as its snapshot condition.
    ``reason``: ``"all_accounted"``, ``"deadline"`` or None.
    """

    action: Literal["none", "complete", "partial"]
    done: frozenset[int]
    failed: frozenset[int]  # failed − done, for display and the PARTIAL write
    add_failed: frozenset[int]  # indexes to ADD to failed_shards (reaper: all − done − failed)
    n_done_seen: int
    reason: str | None = None


def aggregate(shards: Iterable[Mapping], only: Iterable[int] | None = None) -> Result | None:
    """Aggregate shard items (``shard_index``, ``n``, ``sum_hex``, ``sum_sq_hex``) in index order.

    ``only`` restricts to those indexes (the done set, for PARTIAL). N = 0 → None;
    N = 1 → SE and CI None. ``math.fsum``, Bessel variance clamped at 0, ``Z95``.
    """
    keep = None if only is None else {int(i) for i in only}
    rows = sorted(
        (s for s in shards if keep is None or int(s["shard_index"]) in keep),
        key=lambda s: int(s["shard_index"]),
    )
    N = sum(int(s["n"]) for s in rows)
    if N == 0:
        return None
    S = math.fsum(from_hex(s["sum_hex"]) for s in rows)
    if N == 1:
        return Result(n=1, estimate=S, std_error=None, ci95=None)
    Q = math.fsum(from_hex(s["sum_sq_hex"]) for s in rows)
    mean = S / N
    var = max(0.0, (Q - S * mean) / (N - 1))
    se = math.sqrt(var / N)
    return Result(n=N, estimate=mean, std_error=se, ci95=(mean - Z95 * se, mean + Z95 * se))


def decide(job: Mapping, now_epoch: float | None = None) -> Decision:
    """Pure decision from a job item (``status``, ``shards``, ``done_shards``?, ``failed_shards``?,
    ``deadline_at``). Not QUEUED → none. ``|done ∪ failed| = shards`` → complete when nothing
    failed, else partial. Otherwise, when ``now_epoch`` is given and past ``deadline_at`` →
    partial with reason ``"deadline"`` (the reaper). Missing sets are empty.
    """
    done = ns_to_set(job.get("done_shards"))
    failed_all = ns_to_set(job.get("failed_shards"))
    failed = failed_all - done
    none = Decision("none", done, failed, frozenset(), len(done))
    if job.get("status") != "QUEUED":
        return none
    n = int(job["shards"])
    if len(done | failed_all) >= n:
        if not failed_all:
            return Decision("complete", done, failed, frozenset(), len(done), "all_accounted")
        return Decision("partial", done, failed, frozenset(), len(done), "all_accounted")
    if now_epoch is not None and now_epoch > float(job["deadline_at"]):
        missing = frozenset(range(n)) - done
        return Decision("partial", done, missing, missing - failed_all, len(done), "deadline")
    return none
