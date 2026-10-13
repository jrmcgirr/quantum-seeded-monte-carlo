"""Worker Lambda (HANDOFF §7.6, §7.9): simulate one shard, count it exactly once, finalize the job.

Thin by design: every rule (message format, simulation, write expressions, the finalize decision,
aggregation) is in ``qmc_core``. This module only sequences the calls and maps failures.
"""

from __future__ import annotations

import functools
import os
import time

_INIT_T0 = time.perf_counter()

import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from qmc_core import aggregate, codec, ddb, emf, fingerprint, logjson, messages, seeds, sim  # noqa: E402

logjson.configure("worker")
# Fail closed before any message is processed (raises under QMC_ENFORCE_PINNED_DISPATCH=true).
EXP_TARGET = fingerprint.assert_reproducible_dispatch()
FINGERPRINT = fingerprint.fingerprint()


class ForcedFailure(RuntimeError):
    """Raised for a shard listed in ``fault_injection.fail_shards`` when the deployment allows it."""


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _fault_injection_enabled() -> bool:
    return os.environ.get("FAULT_INJECTION_ENABLED", "false").lower() == "true"


_BOTO_CONFIG = Config(retries={"mode": "standard"})


@functools.cache
def _table_for(name: str):
    return boto3.resource("dynamodb", config=_BOTO_CONFIG).Table(name)


@functools.cache
def _sqs():
    return boto3.client("sqs", config=_BOTO_CONFIG)


def _table():
    # Read at call time, never at import, so tests can set it per test (HANDOFF §4.1).
    return _table_for(os.environ["TABLE_NAME"])


logjson.log(
    "init",
    duration_ms=int((time.perf_counter() - _INIT_T0) * 1000),
    exp_target=EXP_TARGET,
    fingerprint=FINGERPRINT,
    config={
        k: os.environ.get(k)
        for k in (
            "QMC_ENV",
            "QMC_BUILD_SHA",
            "QMC_ENFORCE_PINNED_DISPATCH",
            "TABLE_NAME",
            "QUEUE_URL",
            "METRICS_NAMESPACE",
            "MAX_RECEIVE_COUNT",
            "RETRY_VISIBILITY_S",
            "NPY_DISABLE_CPU_FEATURES",
            "FAULT_INJECTION_ENABLED",
            "LOG_LEVEL",
        )
    },
)


def handle(event, context):
    """SQS batch (size 1, but loop anyway) → ``{"batchItemFailures": [...]}``. Never raises."""
    failures = []
    for record in event.get("Records", []):
        try:
            ok = _process(record, context)
        except Exception as exc:  # the failure path itself failed; SQS retries after visibility
            logjson.log("unhandled_error", level="ERROR", error=repr(exc))
            ok = False
        if not ok:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def _process(record, context) -> bool:
    """True when the record is done (written, duplicate, or orphan); False to report it failed."""
    logjson.clear()
    attempt = int(record["attributes"]["ApproximateReceiveCount"])
    logjson.bind(request_id=context.aws_request_id, attempt=attempt)
    try:
        msg = messages.decode(record["body"])
    except messages.MessageError as exc:
        # No job to mark; it reaches the DLQ after MAX_RECEIVE_COUNT receives (§7.6 step 1).
        logjson.log("poison_message", level="ERROR", error=str(exc), message_id=record["messageId"])
        return False
    logjson.bind(job_id=msg.job_id, shard_index=msg.shard_index)
    try:
        _run(msg, attempt, context)
        return True
    except Exception as exc:
        _on_failure(record, msg, attempt, exc)
        return False


def _run(msg: messages.ShardMessage, attempt: int, context) -> None:
    if msg.fail and _fault_injection_enabled():
        raise ForcedFailure(f"fault injection on shard {msg.shard_index}")
    if msg.algo_version != sim.ALGO_VERSION:
        raise ValueError(f"algo_version {msg.algo_version!r} != {sim.ALGO_VERSION!r}")

    t0 = time.perf_counter()
    result = sim.simulate_shard(
        codec.params_from_repr(msg.params),
        msg.shard_paths,
        seeds.master_int_from_hex(msg.master_seed_hex),
        msg.shard_index,
        chunk_size=msg.chunk_size,
        variance_reduction=msg.variance_reduction,
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)

    table = _table()
    try:
        table.put_item(
            **ddb.shard_put(
                msg.job_id,
                msg.shard_index,
                result,
                duration_ms=duration_ms,
                memory_mb=int(context.memory_limit_in_mb),
                attempt=attempt,
                fingerprint=FINGERPRINT,
                now_epoch=time.time(),
            )
        )
        logjson.log("shard_written", n=result.n, duration_ms=duration_ms, exp_target=EXP_TARGET)
        emf.emit("PathsSimulated", result.n, job_id=msg.job_id, shard_index=msg.shard_index)
    except ClientError as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        # First write wins. An earlier attempt may have crashed before mark_done: keep going.
        logjson.log("duplicate_delivery")

    try:
        job = table.update_item(**ddb.mark_done(msg.job_id, msg.shard_index))["Attributes"]
    except ClientError as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        logjson.log("orphan_shard", level="WARNING")
        return
    _finalize(msg.job_id, job)


def _on_failure(record, msg: messages.ShardMessage, attempt: int, exc: Exception) -> None:
    logjson.log("shard_error", level="ERROR", error=repr(exc))
    # Every attempt, the last included: SQS moves it to the DLQ on the *next* receive.
    try:
        _sqs().change_message_visibility(
            QueueUrl=os.environ["QUEUE_URL"],
            ReceiptHandle=record["receiptHandle"],
            VisibilityTimeout=_env_int("RETRY_VISIBILITY_S", 5),
        )
    except Exception as vis_exc:
        logjson.log("visibility_change_failed", level="WARNING", error=repr(vis_exc))

    if attempt < _env_int("MAX_RECEIVE_COUNT", 3):
        return
    table = _table()
    try:
        job = table.update_item(**ddb.mark_failed(msg.job_id, msg.shard_index))["Attributes"]
        logjson.log("shard_marked_failed", level="ERROR")
        emf.emit("ShardFailures", 1, job_id=msg.job_id, shard_index=msg.shard_index)
    except ClientError as cexc:
        if not ddb.is_conditional_failure(cexc):
            raise
        # Already counted done (failed after counting), or the job is gone.
        job = ddb.item_from_error(cexc)
        logjson.log("mark_failed_refused", job_exists=job is not None)
        if job is None:
            return
    _finalize(msg.job_id, job)


def _finalize(job_id: str, job: dict) -> None:
    """Finalize from a job item if ``decide`` says so; on a refusal, re-decide once from ALL_OLD."""
    table = _table()
    for _ in range(2):
        decision = aggregate.decide(job)
        if decision.action == "none":
            return
        shards = _query_shards(table, job_id)
        now = time.time()
        if decision.action == "complete":
            kwargs = ddb.finalize_complete(job_id, aggregate.aggregate(shards), now)
        else:
            partial = aggregate.aggregate(shards, only=decision.done)
            kwargs = ddb.finalize_partial(
                job_id, partial, decision.add_failed, decision.n_done_seen, now
            )
        try:
            table.update_item(**kwargs)
        except ClientError as exc:
            if not ddb.is_conditional_failure(exc):
                raise
            logjson.log("finalize_refused", action=decision.action)
            job = ddb.item_from_error(exc)
            if job is None:
                return
            continue
        status = "COMPLETE" if decision.action == "complete" else "PARTIAL"
        logjson.log("job_finalized", status=status, failed=sorted(decision.failed))
        emf.emit("JobsCompleted" if status == "COMPLETE" else "JobsPartial", 1, job_id=job_id)
        return


def _query_shards(table, job_id: str) -> list[dict]:
    kwargs = ddb.shards_query(job_id)
    items: list[dict] = []
    while True:
        page = table.query(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs = {**kwargs, "ExclusiveStartKey": page["LastEvaluatedKey"]}
