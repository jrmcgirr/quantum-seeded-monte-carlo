"""POST /jobs (HANDOFF §7.5) and POST /jobs/{id}/replay (§7.8)."""

from __future__ import annotations

import time
import uuid

from qmc_api import aws, jobs, qrypt, secret
from qmc_core import codec, ddb, emf, logjson, messages
from qmc_core.errors import EntropyError, ErrorCode, QmcError
from qmc_core.params import (
    ALGO_VERSION,
    CHUNK_SIZE,
    Limits,
    parse_body,
    split_paths,
    validate_job_request,
)
from qmc_core.seeds import derive_master_seed

SQS_RETRIES = 2
LAMBDA_MARGIN_MS = 500


def _take_slot(table, now: float) -> int | None:
    """Claim the next Qrypt call (QRYPT_MIN_GAP_MS after the last one). None when claimed,
    else the Retry-After seconds until the gap ends."""
    gap_ms = aws.env_int("QRYPT_MIN_GAP_MS")
    try:
        table.update_item(**ddb.limiter_gap_take(now, gap_ms))
    except Exception as exc:
        if ddb.is_conditional_failure(exc):
            return ddb.limiter_gap_retry_after(ddb.item_from_error(exc), now, gap_ms)
        raise
    return None


def submit(event, context) -> tuple[int, dict]:
    spec = validate_job_request(
        parse_body(event.get("body"), event.get("isBase64Encoded", False)),
        Limits.from_env(),
        aws.fault_injection_enabled(),
    )
    job_id = uuid.uuid4().hex
    logjson.bind(job_id=job_id)
    table = aws.table()

    now = time.time()
    wait_s = _take_slot(table, now)
    if wait_s is not None:
        raise QmcError(ErrorCode.ENTROPY_RATE_LIMITED, retry_after=wait_s)

    # Fail closed: until the entropy claim succeeds, nothing but the limiter slot is written.
    try:
        token = secret.get_token()
        budget = min(
            aws.env_int("QRYPT_DEADLINE_MS"),
            context.get_remaining_time_in_millis() - LAMBDA_MARGIN_MS,
        )
        entropy = qrypt.get_entropy_source().fetch(
            token, budget, lambda: _take_slot(table, time.time()) is None
        )
        emf.emit("EntropyKiB", 1, job_id=job_id)
        seed = derive_master_seed(entropy.block, job_id)
        try:
            table.put_item(**ddb.entropy_claim(seed.raw_sha256, job_id, time.time()))
        except Exception as exc:
            if ddb.is_conditional_failure(exc):
                raise EntropyError(
                    ErrorCode.QRYPT_BAD_RESPONSE, "Qrypt returned a block already used."
                ) from None
            raise
    except QmcError as exc:
        emf.emit("SeedingFailures", 1, cause=exc.code.name, job_id=job_id)
        logjson.log("seeding_failed", level="ERROR", cause=exc.code.name)
        raise

    now = time.time()
    item = ddb.new_job_item(
        job_id=job_id,
        params=codec.params_to_repr(spec.params()),
        paths=spec.paths,
        shards=spec.shards,
        paths_per_shard=split_paths(spec.paths, spec.shards, spec.variance_reduction),
        chunk_size=CHUNK_SIZE,
        variance_reduction=spec.variance_reduction,
        algo_version=ALGO_VERSION,
        master_seed_hex=seed.hex,
        seed_provenance=entropy.provenance(seed),
        now_epoch=now,
        deadline_s=aws.env_int("JOB_DEADLINE_S"),
        ttl_days=aws.env_int("JOB_TTL_DAYS"),
        fault_injection={"fail_shards": list(spec.fail_shards)} if spec.fail_shards else None,
    )
    _create(table, item)
    emf.emit("JobsSubmitted", 1, job_id=job_id)
    return 202, _accepted(item)


def replay(event, context, original_id: str) -> tuple[int, dict]:
    table = aws.table()
    original = jobs.settled_job(table, original_id)  # 404; finalizes a done-but-QUEUED original
    if original["status"] != "COMPLETE":
        raise QmcError(
            ErrorCode.JOB_NOT_REPLAYABLE,
            f"Job {original_id} is {original['status']}; only a COMPLETE job can be replayed.",
        )
    job_id = uuid.uuid4().hex
    logjson.bind(job_id=job_id)
    # No limiter, no Qrypt, no entropy claim; fault_injection is not copied.
    item = ddb.new_job_item(
        job_id=job_id,
        params=dict(original["params"]),
        paths=int(original["paths"]),
        shards=int(original["shards"]),
        paths_per_shard=[int(n) for n in original["paths_per_shard"]],
        chunk_size=int(original["chunk_size"]),
        variance_reduction=original["variance_reduction"],
        algo_version=original["algo_version"],
        master_seed_hex=original["master_seed_hex"],
        seed_provenance={"source": "replay", "replay_of": original_id},
        now_epoch=time.time(),
        deadline_s=aws.env_int("JOB_DEADLINE_S"),
        ttl_days=aws.env_int("JOB_TTL_DAYS"),
        replay_of=original_id,
    )
    _create(table, item)
    emf.emit("JobsSubmitted", 1, job_id=job_id, replay_of=original_id)
    return 202, _accepted(item)


def _accepted(item: dict) -> dict:
    job_id = item["job_id"]
    body = {
        "job_id": job_id,
        "status": "QUEUED",
        "paths": int(item["paths"]),
        "shards": int(item["shards"]),
        "master_seed_hex": item["master_seed_hex"],
        "links": {"self": f"/jobs/{job_id}", "replay": f"/jobs/{job_id}/replay"},
    }
    if item.get("replay_of"):
        body["replay_of"] = item["replay_of"]
    return body


def _create(table, item: dict) -> None:
    """``job_put``, then JOBLIST and enqueue. Any failure after the job exists marks it FAILED,
    so no job is left QUEUED without its messages."""
    table.put_item(**ddb.job_put(item))
    try:
        table.put_item(**ddb.joblist_put(item))
        _enqueue(messages.for_job(item))
    except Exception as exc:
        reason = type(exc).__name__
        logjson.log("enqueue_failed", level="ERROR", reason=reason)
        try:
            table.update_item(**ddb.mark_enqueue_failed(item["job_id"], reason))
        except Exception as mark_exc:
            logjson.log("mark_enqueue_failed_failed", level="ERROR", error=type(mark_exc).__name__)
        raise QmcError(ErrorCode.ENQUEUE_FAILED) from None


def _enqueue(msgs: list[messages.ShardMessage]) -> None:
    queue_url = aws.env("QUEUE_URL")
    for entries in messages.batch_entries(msgs):
        for _ in range(1 + SQS_RETRIES):
            resp = aws.sqs().send_message_batch(QueueUrl=queue_url, Entries=entries)
            failed = {f["Id"] for f in resp.get("Failed", [])}
            entries = [e for e in entries if e["Id"] in failed]
            if not entries:
                break
        else:
            raise RuntimeError(f"{len(entries)} shard messages not sent after retries")
