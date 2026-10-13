"""Opt-in PENDING_ENTROPY seeding (contract change 008, QMC-128).

A submit with ``on_entropy_failure: "queue"`` that hits a retryable seeding failure stores an
unseeded PENDING_ENTROPY job and self-invokes asynchronously with ``{"qmc_seed", "attempt"}``.
Each attempt backs off, then runs the same seeding as submit (§7.5 steps 2–5). Success moves the
job to QUEUED with a Qrypt-derived seed and enqueues it; failures retry up to
PENDING_MAX_ATTEMPTS, then end FAILED naming the cause. Every transition is conditional on
``status = PENDING_ENTROPY``, so the seeder and GET's expiry backstop can race safely.

A refused limiter slot (our own ENTROPY_RATE_LIMITED: another seeder or submit holds the 2 s gap)
is not an attempt: the seeder waits for the gap plus 0–2 s of jitter, in process while that fits,
else in a fresh invocation (same attempt, ``not_before_ms``, ``hop + 1``). Lambda stops a
self-invoke chain at about 16 hops, so hop MAX_HOPS fails the job instead of re-invoking.
"""

from __future__ import annotations

import json
import random
import time

from qmc_api import aws, jobs, submit
from qmc_core import ddb, emf, logjson
from qmc_core.errors import RETRYABLE_ENTROPY_CODES, ErrorCode, QmcError
from qmc_core.params import is_job_id

PENDING = "PENDING_ENTROPY"
WORK_RESERVE_MS = 1000  # after the Qrypt budget: claim, two writes, enqueue
JITTER_S = 2.0  # spreads seeders woken by the same gap end
MAX_HOPS = 15  # Lambda's recursion detection drops the ~16th self-invoke of a chain


def _sleep(seconds: float) -> None:
    """The backoff sleep (module-level so tests can patch it)."""
    time.sleep(seconds)


def invoke_seeder(job_id: str, attempt: int, **wait) -> None:
    """``wait`` is ``not_before_ms`` and ``hop`` for a slot-wait re-invoke."""
    aws.lambda_().invoke(
        FunctionName=aws.env("AWS_LAMBDA_FUNCTION_NAME"),
        InvocationType="Event",
        Payload=json.dumps({"qmc_seed": job_id, "attempt": attempt, **wait}).encode(),
    )


def fail_pending(table, job_id: str, cause: ErrorCode | str) -> None:
    """``pending_fail`` plus ``SeedingFailures``. A refusal means the job already left
    PENDING_ENTROPY (seeded, or failed by the other path): nothing to do."""
    name = cause if isinstance(cause, str) else cause.name
    try:
        table.update_item(**ddb.pending_fail(job_id, name))
    except Exception as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        logjson.log("pending_fail_refused", cause=name)
        return
    logjson.log("seeding_failed", level="ERROR", cause=name)
    emf.emit("SeedingFailures", 1, cause=name, job_id=job_id)


def backoff_s(attempt: int, context) -> float:
    """``PENDING_BACKOFF_MS[attempt-1]`` (the last value repeats), bounded so the attempt still
    fits in the remaining Lambda time."""
    steps = [int(x) for x in aws.env("PENDING_BACKOFF_MS").split(",")]
    wanted = steps[min(attempt, len(steps)) - 1]
    return max(0, min(wanted / 1000, room_s(context)))


def room_s(context) -> float:
    """How long the seeder may still sleep and then make its Qrypt call and writes."""
    return (
        context.get_remaining_time_in_millis()
        - aws.env_int("QRYPT_DEADLINE_MS")
        - submit.LAMBDA_MARGIN_MS
        - WORK_RESERVE_MS
    ) / 1000


def run(event, context) -> None:
    """The seeder entry point (an async self-invoke). Never raises: a Lambda error would trip
    the api-errors alarm, and async retries are off."""
    job_id = event.get("qmc_seed")
    try:
        _attempt(
            job_id,
            int(event["attempt"]),
            context,
            event.get("not_before_ms"),
            int(event.get("hop", 1)),
        )
    except Exception as exc:
        logjson.log("seeder_error", level="ERROR", error=type(exc).__name__, detail=str(exc))
        if is_job_id(job_id):
            try:
                fail_pending(aws.table(), job_id, ErrorCode.INTERNAL_ERROR)
            except Exception as fail_exc:
                logjson.log("pending_fail_failed", level="ERROR", error=type(fail_exc).__name__)


def _attempt(job_id, attempt: int, context, not_before_ms=None, hop: int = 1) -> None:
    logjson.bind(job_id=job_id, attempt=attempt, hop=hop)
    if not is_job_id(job_id):
        logjson.log("seed_skipped", level="WARNING", reason="bad job id")
        return
    table = aws.table()
    if not _still_pending(table, job_id):
        return
    if not_before_ms is None:
        _sleep(backoff_s(attempt, context))
    else:
        _sleep(max(0, min(int(not_before_ms) / 1000 - time.time(), room_s(context))))
    while True:
        try:
            seed, entropy = submit.seed_job(table, job_id, context)
            break
        except QmcError as exc:
            if exc.code is not ErrorCode.ENTROPY_RATE_LIMITED:
                _failed(table, job_id, attempt, exc.code)
                return
            wait_s = (exc.retry_after or 1) + random.uniform(0, JITTER_S)
        logjson.log("slot_wait", wait_s=round(wait_s, 3))
        if wait_s > room_s(context):
            _wait_elsewhere(table, job_id, attempt, hop, wait_s)
            return
        _sleep(wait_s)
        # Re-read: a duplicate delivery of this attempt may have seeded it meanwhile.
        if not _still_pending(table, job_id):
            return

    try:
        queued = table.update_item(
            **ddb.pending_to_queued(
                job_id,
                seed.hex,
                entropy.provenance(seed),
                time.time(),
                aws.env_int("JOB_DEADLINE_S"),
            )
        )["Attributes"]
    except Exception as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        logjson.log("pending_to_queued_refused")  # GET's expiry backstop failed it first
        return
    logjson.log("entropy_seeded")
    try:
        # The ALL_NEW item: no read between QUEUED and publish, whose failure handling marks
        # the job FAILED, so a QUEUED job never lacks its messages.
        submit.publish(table, queued, joblist=False)
    except QmcError:
        return  # already marked FAILED and logged by publish
    emf.emit("JobsSubmitted", 1, job_id=job_id, pending_attempt=attempt)


def _still_pending(table, job_id: str) -> bool:
    """False (logged, or failed past ``pending_until``) once the job needs no seeding."""
    job = jobs.read(table, job_id)
    if job is None or job["status"] != PENDING:
        logjson.log("seed_skipped", reason=job["status"] if job else "missing")
        return False
    if time.time() > int(job["pending_until"]):
        fail_pending(table, job_id, job.get("last_entropy_error") or "PENDING_EXPIRED")
        return False
    return True


def _wait_elsewhere(table, job_id: str, attempt: int, hop: int, wait_s: float) -> None:
    """Carry a slot wait that does not fit this invocation to the next one."""
    code = ErrorCode.ENTROPY_RATE_LIMITED
    if hop >= MAX_HOPS:
        logjson.log("seeder_hops_exhausted", level="ERROR")
        fail_pending(table, job_id, code)
        return
    try:
        invoke_seeder(
            job_id, attempt, not_before_ms=int((time.time() + wait_s) * 1000), hop=hop + 1
        )
    except Exception as exc:
        logjson.log("seeder_invoke_failed", level="ERROR", error=type(exc).__name__)
        fail_pending(table, job_id, code)


def _failed(table, job_id: str, attempt: int, code: ErrorCode) -> None:
    logjson.log("entropy_attempt_failed", cause=code.name)
    if code not in RETRYABLE_ENTROPY_CODES:
        fail_pending(table, job_id, code)
        return
    try:
        attempts = int(
            table.update_item(**ddb.pending_attempt_failed(job_id, code.name))["Attributes"][
                "entropy_attempts"
            ]
        )
    except Exception as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        logjson.log("pending_attempt_refused", cause=code.name)
        return
    # Async invocation is at-least-once: the submit's failure is attempt 0's count of 1, so
    # seeder attempt n leaves n + 1. Any other count means a duplicate run got here first.
    if attempts != attempt + 1:
        logjson.log("duplicate_seeder", entropy_attempts=attempts)
        return
    if attempt >= aws.env_int("PENDING_MAX_ATTEMPTS"):
        fail_pending(table, job_id, code)
        return
    try:
        invoke_seeder(job_id, attempt + 1)
    except Exception as exc:
        logjson.log("seeder_invoke_failed", level="ERROR", error=type(exc).__name__)
        fail_pending(table, job_id, code)
