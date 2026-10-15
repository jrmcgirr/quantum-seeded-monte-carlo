"""DynamoDB keys, item shapes and the exact write expressions (HANDOFF §6).

Every builder returns the keyword arguments for the boto3 **resource** ``Table`` method named
in its docstring (``table.update_item(**ddb.mark_done(job_id, 3))``), except ``job_statuses``,
which is for the service resource's ``batch_get_item``. Every attribute name goes through
``ExpressionAttributeNames``. No Python float ever reaches boto3.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from qmc_core.aggregate import Result, ShardResult
from qmc_core.codec import iso_ms, to_hex

JOBLIST_PK = "JOBLIST"
TTL_DAYS = 365
# Qrypt enforces about 1 call/s, so the default window is 1 s (contract change 005; was 10).
LIMITER_WINDOW_S = 1
_DAY = 86_400


def _ttl(now_epoch: float, days: int = TTL_DAYS) -> int:
    return int(now_epoch) + days * _DAY


def limiter_window(now_epoch: float, size_s: int = LIMITER_WINDOW_S) -> int:
    """Start (epoch seconds) of the fixed window containing ``now``: ``floor(now / size_s) * size_s``.
    ``Retry-After`` is ``window + size_s - now`` rounded up (at least 1)."""
    return int(now_epoch // size_s) * size_s


def job_key(job_id: str) -> dict[str, str]:
    """``{"pk": "JOB#<id>", "sk": "META"}``."""
    return {"pk": f"JOB#{job_id}", "sk": "META"}


def shard_key(job_id: str, shard_index: int) -> dict[str, str]:
    """``{"pk": "JOB#<id>", "sk": "SHARD#0003"}``."""
    return {"pk": f"JOB#{job_id}", "sk": f"SHARD#{shard_index:04d}"}


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
    item = {
        **job_key(job_id),
        "job_id": job_id,
        "status": "QUEUED",
        "created_at": iso_ms(now_epoch),
        "params": dict(params),
        "paths": paths,
        "shards": shards,
        "paths_per_shard": list(paths_per_shard),
        "chunk_size": chunk_size,
        "variance_reduction": variance_reduction,
        "algo_version": algo_version,
        "master_seed_hex": master_seed_hex,
        "seed_provenance": dict(seed_provenance),
        "deadline_at": int(now_epoch) + deadline_s,
        "ttl": _ttl(now_epoch, ttl_days),
    }
    if replay_of is not None:
        item["replay_of"] = replay_of
    if fault_injection:
        item["fault_injection"] = dict(fault_injection)
    return item


def new_pending_job_item(
    *,
    job_id: str,
    params: Mapping[str, str],
    paths: int,
    shards: int,
    paths_per_shard: list[int],
    chunk_size: int,
    variance_reduction: str,
    algo_version: str,
    now_epoch: float,
    max_age_s: int,
    last_error: str,
    ttl_days: int = TTL_DAYS,
    fault_injection: Mapping | None = None,
) -> dict:
    """An unseeded ``PENDING_ENTROPY`` job item (contract change 008): no ``master_seed_hex``,
    ``seed_provenance`` or ``deadline_at``; written with ``job_put``."""
    item = {
        **job_key(job_id),
        "job_id": job_id,
        "status": "PENDING_ENTROPY",
        "created_at": iso_ms(now_epoch),
        "params": dict(params),
        "paths": paths,
        "shards": shards,
        "paths_per_shard": list(paths_per_shard),
        "chunk_size": chunk_size,
        "variance_reduction": variance_reduction,
        "algo_version": algo_version,
        "entropy_attempts": 1,
        "last_entropy_error": last_error,
        "pending_until": int(now_epoch) + max_age_s,
        "ttl": _ttl(now_epoch, ttl_days),
    }
    if fault_injection:
        item["fault_injection"] = dict(fault_injection)
    return item


def pending_attempt_failed(job_id: str, error_code: str) -> dict:
    """``update_item``: ``ADD #attempts :one SET #err = :e``, condition ``#s = :pending``, ``ALL_NEW``."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "ADD #attempts :one SET #err = :e",
        "ConditionExpression": "#s = :pending",
        "ExpressionAttributeNames": {
            "#attempts": "entropy_attempts",
            "#err": "last_entropy_error",
            "#s": "status",
        },
        "ExpressionAttributeValues": {":one": 1, ":e": error_code, ":pending": "PENDING_ENTROPY"},
        "ReturnValues": "ALL_NEW",
    }


def pending_to_queued(
    job_id: str,
    master_seed_hex: str,
    seed_provenance: Mapping,
    now_epoch: float,
    deadline_s: int,
) -> dict:
    """``update_item``: ``SET #s = :queued, #seed, #prov, #deadline = now + deadline_s`` (the
    deadline starts at seeding), condition ``#s = :pending``, ``ALL_NEW``; ``ALL_OLD`` on failure."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "SET #s = :queued, #seed = :seed, #prov = :prov, #deadline = :deadline",
        "ConditionExpression": "#s = :pending",
        "ExpressionAttributeNames": {
            "#s": "status",
            "#seed": "master_seed_hex",
            "#prov": "seed_provenance",
            "#deadline": "deadline_at",
        },
        "ExpressionAttributeValues": {
            ":queued": "QUEUED",
            ":pending": "PENDING_ENTROPY",
            ":seed": master_seed_hex,
            ":prov": dict(seed_provenance),
            ":deadline": int(now_epoch) + deadline_s,
        },
        "ReturnValues": "ALL_NEW",
        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }


def pending_fail(job_id: str, reason: str) -> dict:
    """``update_item``: ``SET #s = :failed, #why = :why``, condition ``#s = :pending``."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "SET #s = :failed, #why = :why",
        "ConditionExpression": "#s = :pending",
        "ExpressionAttributeNames": {"#s": "status", "#why": "fail_reason"},
        "ExpressionAttributeValues": {
            ":failed": "FAILED",
            ":pending": "PENDING_ENTROPY",
            ":why": reason,
        },
    }


def limiter_take(window: int, cap: int, now_epoch: float, size_s: int = LIMITER_WINDOW_S) -> dict:
    """``update_item``: ``ADD #n :one SET #ttl = :ttl`` on ``RL#qrypt/<window>``, condition
    ``attribute_not_exists(#n) OR #n < :cap``."""
    return {
        "Key": {"pk": "RL#qrypt", "sk": str(window)},
        "UpdateExpression": "ADD #n :one SET #ttl = :ttl",
        "ConditionExpression": "attribute_not_exists(#n) OR #n < :cap",
        "ExpressionAttributeNames": {"#n": "n", "#ttl": "ttl"},
        "ExpressionAttributeValues": {
            ":one": 1,
            ":cap": cap,
            ":ttl": int(window) + size_s + 3600,
        },
    }


LIMITER_GAP_KEY = {"pk": "RL#qrypt", "sk": "LAST"}


def limiter_gap_take(now_epoch: float, gap_ms: int) -> dict:
    """``update_item`` on ``RL#qrypt/LAST``: ``SET #at = :now, #ttl = :ttl``, condition
    ``attribute_not_exists(#at) OR #at <= :cutoff`` (``:cutoff = now_ms - gap_ms``), so two Qrypt
    calls are always at least ``gap_ms`` apart (contract change 006). Times are integer epoch ms.
    ``ReturnValuesOnConditionCheckFailure=ALL_OLD`` hands a refusal the last call time for
    ``limiter_gap_retry_after``."""
    now_ms = int(now_epoch * 1000)
    return {
        "Key": dict(LIMITER_GAP_KEY),
        "UpdateExpression": "SET #at = :now, #ttl = :ttl",
        "ConditionExpression": "attribute_not_exists(#at) OR #at <= :cutoff",
        "ExpressionAttributeNames": {"#at": "at_ms", "#ttl": "ttl"},
        "ExpressionAttributeValues": {
            ":now": now_ms,
            ":cutoff": now_ms - int(gap_ms),
            ":ttl": int(now_epoch) + 3600,
        },
        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }


def limiter_gap_retry_after(last_item: Mapping | None, now_epoch: float, gap_ms: int) -> int:
    """Whole seconds until the gap ends (at least 1), from the refused ``RL#qrypt/LAST`` item."""
    if not last_item or "at_ms" not in last_item:
        return 1
    remaining_ms = int(last_item["at_ms"]) + int(gap_ms) - int(now_epoch * 1000)
    return max(1, -(-remaining_ms // 1000))


def entropy_claim(raw_sha256: str, job_id: str, now_epoch: float) -> dict:
    """``put_item`` of ``ENTROPY#<sha>/ENTROPY``, condition ``attribute_not_exists(pk)``."""
    return {
        "Item": {
            "pk": f"ENTROPY#{raw_sha256}",
            "sk": "ENTROPY",
            "job_id": job_id,
            "fetched_at": iso_ms(now_epoch),
            "ttl": _ttl(now_epoch),
        },
        "ConditionExpression": "attribute_not_exists(#pk)",
        "ExpressionAttributeNames": {"#pk": "pk"},
    }


def job_put(job_item: Mapping) -> dict:
    """``put_item`` of a ``new_job_item``, condition ``attribute_not_exists(pk)``."""
    return {
        "Item": dict(job_item),
        "ConditionExpression": "attribute_not_exists(#pk)",
        "ExpressionAttributeNames": {"#pk": "pk"},
    }


def joblist_put(job_item: Mapping) -> dict:
    """``put_item`` of the ``JOBLIST/<created_at>#<job_id>`` entry for a job item."""
    item = {
        "pk": JOBLIST_PK,
        "sk": f"{job_item['created_at']}#{job_item['job_id']}",
        "job_id": job_item["job_id"],
        "status_at_submit": job_item["status"],
        "paths": job_item["paths"],
        "shards": job_item["shards"],
        "ttl": job_item["ttl"],
    }
    if job_item.get("replay_of") is not None:
        item["replay_of"] = job_item["replay_of"]
    return {"Item": item}


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
    invocation_ms: int | None = None,
    init_ms: int | None = None,
) -> dict:
    """``put_item`` of the shard item (sums as hex), condition ``attribute_not_exists(pk)``.
    A conditional failure is a duplicate delivery, not an error. ``invocation_ms`` (handler
    elapsed time up to this put) and ``init_ms`` (cold-start init, first record of an execution
    environment only, else 0) feed ``cost.estimate`` (contract change 009)."""
    extra = {
        k: int(v)
        for k, v in (("invocation_ms", invocation_ms), ("init_ms", init_ms))
        if v is not None
    }
    return {
        "Item": {
            **shard_key(job_id, shard_index),
            "shard_index": shard_index,
            "n": int(result.n),
            "sum_hex": to_hex(result.sum),
            "sum_sq_hex": to_hex(result.sum_sq),
            "duration_ms": int(duration_ms),
            "memory_mb": int(memory_mb),
            "attempt": int(attempt),
            "fingerprint": dict(fingerprint),
            "completed_at": iso_ms(now_epoch),
            "ttl": _ttl(now_epoch),
            **extra,
        },
        "ConditionExpression": "attribute_not_exists(#pk)",
        "ExpressionAttributeNames": {"#pk": "pk"},
    }


def mark_done(job_id: str, shard_index: int) -> dict:
    """``update_item``: ``ADD #done :i``, condition ``attribute_exists(pk)``, ``ALL_NEW``."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "ADD #done :i",
        "ConditionExpression": "attribute_exists(#pk)",
        "ExpressionAttributeNames": {"#done": "done_shards", "#pk": "pk"},
        "ExpressionAttributeValues": {":i": {shard_index}},
        "ReturnValues": "ALL_NEW",
    }


def mark_failed(job_id: str, shard_index: int) -> dict:
    """``update_item``: ``ADD #failed :i``, condition ``attribute_exists(pk) AND
    (attribute_not_exists(#done) OR NOT contains(#done, :i))``, ``ALL_NEW``, and
    ``ReturnValuesOnConditionCheckFailure=ALL_OLD``."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "ADD #failed :iset",
        "ConditionExpression": (
            "attribute_exists(#pk) AND (attribute_not_exists(#done) OR NOT contains(#done, :i))"
        ),
        "ExpressionAttributeNames": {
            "#failed": "failed_shards",
            "#done": "done_shards",
            "#pk": "pk",
        },
        "ExpressionAttributeValues": {":iset": {shard_index}, ":i": shard_index},
        "ReturnValues": "ALL_NEW",
        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }


def finalize_complete(job_id: str, result: Result, now_epoch: float) -> dict:
    """``update_item``: ``SET #s = :complete, #result = :r, #completed = :now``, condition
    ``#s = :queued AND attribute_not_exists(#failed)``, ``ALL_OLD`` on condition failure."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "SET #s = :complete, #result = :r, #completed = :now",
        "ConditionExpression": "#s = :queued AND attribute_not_exists(#failed)",
        "ExpressionAttributeNames": {
            "#s": "status",
            "#result": "result",
            "#completed": "completed_at",
            "#failed": "failed_shards",
        },
        "ExpressionAttributeValues": {
            ":complete": "COMPLETE",
            ":queued": "QUEUED",
            ":r": result.to_item(),
            ":now": iso_ms(now_epoch),
        },
        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }


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
    failed = {int(i) for i in add_failed}
    names = {"#s": "status", "#partial": "partial_result", "#completed": "completed_at"}
    values: dict = {
        ":partial": "PARTIAL",
        ":queued": "QUEUED",
        ":p": None if partial is None else partial.to_item(),
        ":now": iso_ms(now_epoch),
    }
    update = "SET #s = :partial, #partial = :p, #completed = :now"
    if failed:
        update += " ADD #failed :f"
        names["#failed"] = "failed_shards"
        values[":f"] = failed
    names["#done"] = "done_shards"
    if n_done_seen:
        condition = "#s = :queued AND size(#done) = :n_done_seen"
        values[":n_done_seen"] = n_done_seen
    else:
        condition = "#s = :queued AND attribute_not_exists(#done)"
    return {
        "Key": job_key(job_id),
        "UpdateExpression": update,
        "ConditionExpression": condition,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }


def mark_enqueue_failed(job_id: str, reason: str) -> dict:
    """``update_item``: ``SET #s = :failed, #why = :why``, condition ``#s = :queued``."""
    return {
        "Key": job_key(job_id),
        "UpdateExpression": "SET #s = :failed, #why = :why",
        "ConditionExpression": "#s = :queued",
        "ExpressionAttributeNames": {"#s": "status", "#why": "fail_reason"},
        "ExpressionAttributeValues": {":failed": "FAILED", ":queued": "QUEUED", ":why": reason},
    }


def shards_query(job_id: str) -> dict:
    """``query``: ``pk = JOB#id AND begins_with(sk, "SHARD#")``, ``ConsistentRead=True``.
    The caller follows ``LastEvaluatedKey``."""
    return {
        "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
        "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
        "ExpressionAttributeValues": {":pk": f"JOB#{job_id}", ":prefix": "SHARD#"},
        "ConsistentRead": True,
    }


def job_get(job_id: str) -> dict:
    """``get_item`` ``JOB#id/META``, ``ConsistentRead=True`` (API only)."""
    return {"Key": job_key(job_id), "ConsistentRead": True}


def joblist_query(limit: int = 50) -> dict:
    """``query`` of ``JOBLIST``, newest first (``ScanIndexForward=False``), ``Limit``."""
    return {
        "KeyConditionExpression": "#pk = :pk",
        "ExpressionAttributeNames": {"#pk": "pk"},
        "ExpressionAttributeValues": {":pk": JOBLIST_PK},
        "ScanIndexForward": False,
        "Limit": limit,
    }


def job_statuses(table_name: str, job_ids: Iterable[str]) -> dict:
    """Service-resource ``batch_get_item`` for up to 100 ids, projection ``job_id, #s,
    pending_until`` (the last only on PENDING_ENTROPY jobs; contract change 010)."""
    ids = list(dict.fromkeys(job_ids))
    if len(ids) > 100:
        raise ValueError("BatchGetItem takes at most 100 keys")
    return {
        "RequestItems": {
            table_name: {
                "Keys": [job_key(i) for i in ids],
                "ProjectionExpression": "#id, #s, #pu",
                "ExpressionAttributeNames": {
                    "#id": "job_id",
                    "#s": "status",
                    "#pu": "pending_until",
                },
            }
        }
    }


def health_get() -> dict:
    """``get_item`` of ``HEALTH/HEALTH`` (never written; proves reachability)."""
    return {"Key": {"pk": "HEALTH", "sk": "HEALTH"}}


def item_from_error(exc: Exception) -> dict | None:
    """The item a ``ConditionalCheckFailedException`` returned (``ALL_OLD``), deserialized to the
    resource API's Python types; None if absent."""
    raw = (getattr(exc, "response", None) or {}).get("Item")
    if not raw:
        return None
    from boto3.dynamodb.types import TypeDeserializer

    des = TypeDeserializer()
    return {k: des.deserialize(v) for k, v in raw.items()}


def is_conditional_failure(exc: Exception) -> bool:
    """True for a botocore ``ClientError`` with code ``ConditionalCheckFailedException``."""
    response = getattr(exc, "response", None) or {}
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
