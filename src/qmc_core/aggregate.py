"""Shard aggregation and the finalize decision (HANDOFF §7.4, §7.7).

``aggregate`` is the only place a job result is computed; ``decide`` is the only place the
COMPLETE/PARTIAL decision is made. The worker and the API's lazy finalize both call them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

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
        raise NotImplementedError

    def to_view(self) -> dict:
        """API view (§5.4): floats plus their hex strings."""
        raise NotImplementedError

    @classmethod
    def from_item(cls, item: Mapping) -> Result:
        """Inverse of ``to_item`` (accepts boto3's Decimal for ``n``)."""
        raise NotImplementedError


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
    raise NotImplementedError


def decide(job: Mapping, now_epoch: float | None = None) -> Decision:
    """Pure decision from a job item (``status``, ``shards``, ``done_shards``?, ``failed_shards``?,
    ``deadline_at``). Not QUEUED → none. ``|done ∪ failed| = shards`` → complete when nothing
    failed, else partial. Otherwise, when ``now_epoch`` is given and past ``deadline_at`` →
    partial with reason ``"deadline"`` (the reaper). Missing sets are empty.
    """
    raise NotImplementedError
