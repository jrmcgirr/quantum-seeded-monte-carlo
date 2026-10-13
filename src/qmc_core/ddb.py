"""DynamoDB keys, item shapes and the exact write expressions (HANDOFF §6).

Every builder returns the keyword arguments for the boto3 **resource** ``Table`` method named
in its docstring (``table.update_item(**ddb.mark_done(job_id, 3))``), except ``job_statuses``,
which is for the service resource's ``batch_get_item``. Every attribute name goes through
``ExpressionAttributeNames``. No Python float ever reaches boto3.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from qmc_core.aggregate import Result, ShardResult

JOBLIST_PK = "JOBLIST"
TTL_DAYS = 365


def job_key(job_id: str) -> dict[str, str]:
    """``{"pk": "JOB#<id>", "sk": "META"}``."""
    raise NotImplementedError


def shard_key(job_id: str, shard_index: int) -> dict[str, str]:
    """``{"pk": "JOB#<id>", "sk": "SHARD#0003"}``."""
    raise NotImplementedError


def new_job_item(
    *,
    job_id: str,
    params: Mapping[str, str],
    paths: int,
    shards: int,
    paths_per_shard: list[int],
    chunk_size: int,
    variance_reduction: str,
    algo_version: str,
    master_seed_hex: str,
    seed_provenance: Mapping,
    now_epoch: float,
    deadline_s: int,
    ttl_days: int = TTL_DAYS,
    replay_of: str | None = None,
    fault_injection: Mapping | None = None,
) -> dict:
    """The job item of §6.1 with status QUEUED (``params`` already repr strings, see codec)."""
    raise NotImplementedError


def limiter_take(window: int, cap: int, now_epoch: float) -> dict:
    """``update_item``: ``ADD #n :one SET #ttl = :ttl`` on ``RL#qrypt/<window>``, condition
    ``attribute_not_exists(#n) OR #n < :cap``."""
    raise NotImplementedError


def entropy_claim(raw_sha256: str, job_id: str, now_epoch: float) -> dict:
    """``put_item`` of ``ENTROPY#<sha>/ENTROPY``, condition ``attribute_not_exists(pk)``."""
    raise NotImplementedError


def job_put(job_item: Mapping) -> dict:
    """``put_item`` of a ``new_job_item``, condition ``attribute_not_exists(pk)``."""
    raise NotImplementedError


def joblist_put(job_item: Mapping) -> dict:
    """``put_item`` of the ``JOBLIST/<created_at>#<job_id>`` entry for a job item."""
    raise NotImplementedError


def shard_put(
    job_id: str,
    shard_index: int,
    result: ShardResult,
    *,
    duration_ms: int,
    memory_mb: int,
    attempt: int,
    fingerprint: Mapping,
    now_epoch: float,
) -> dict:
    """``put_item`` of the shard item (sums as hex), condition ``attribute_not_exists(pk)``.
    A conditional failure is a duplicate delivery, not an error."""
    raise NotImplementedError


def mark_done(job_id: str, shard_index: int) -> dict:
    """``update_item``: ``ADD #done :i``, condition ``attribute_exists(pk)``, ``ALL_NEW``."""
    raise NotImplementedError


def mark_failed(job_id: str, shard_index: int) -> dict:
    """``update_item``: ``ADD #failed :i``, condition ``attribute_exists(pk) AND
    (attribute_not_exists(#done) OR NOT contains(#done, :i))``, ``ALL_NEW``, and
    ``ReturnValuesOnConditionCheckFailure=ALL_OLD``."""
    raise NotImplementedError


def finalize_complete(job_id: str, result: Result, now_epoch: float) -> dict:
    """``update_item``: ``SET #s = :complete, #result = :r, #completed = :now``, condition
    ``#s = :queued AND attribute_not_exists(#failed)``, ``ALL_OLD`` on condition failure."""
    raise NotImplementedError


def finalize_partial(
    job_id: str,
    partial: Result | None,
    add_failed: Iterable[int],
    n_done_seen: int,
    now_epoch: float,
) -> dict:
    """``update_item``: ``SET #s = :partial, #partial_result = :p, #completed = :now [ADD #failed
    :f]`` (no ADD when empty), condition ``#s = :queued AND size(#done) = :n_done_seen`` (or
    ``attribute_not_exists(#done)`` when 0), ``ALL_OLD`` on condition failure."""
    raise NotImplementedError


def mark_enqueue_failed(job_id: str, reason: str) -> dict:
    """``update_item``: ``SET #s = :failed, #why = :why``, condition ``#s = :queued``."""
    raise NotImplementedError


def shards_query(job_id: str) -> dict:
    """``query``: ``pk = JOB#id AND begins_with(sk, "SHARD#")``, ``ConsistentRead=True``.
    The caller follows ``LastEvaluatedKey``."""
    raise NotImplementedError


def job_get(job_id: str) -> dict:
    """``get_item`` ``JOB#id/META``, ``ConsistentRead=True`` (API only)."""
    raise NotImplementedError


def joblist_query(limit: int = 50) -> dict:
    """``query`` of ``JOBLIST``, newest first (``ScanIndexForward=False``), ``Limit``."""
    raise NotImplementedError


def job_statuses(table_name: str, job_ids: Iterable[str]) -> dict:
    """Service-resource ``batch_get_item`` for up to 100 ids, projection ``job_id, #s``."""
    raise NotImplementedError


def health_get() -> dict:
    """``get_item`` of ``HEALTH/HEALTH`` (never written; proves reachability)."""
    raise NotImplementedError


def item_from_error(exc: Exception) -> dict | None:
    """The item a ``ConditionalCheckFailedException`` returned (``ALL_OLD``), deserialized to the
    resource API's Python types; None if absent."""
    raise NotImplementedError


def is_conditional_failure(exc: Exception) -> bool:
    """True for a botocore ``ClientError`` with code ``ConditionalCheckFailedException``."""
    raise NotImplementedError
