"""Opt-in PENDING_ENTROPY seeding (contract change 008, QMC-128).

A submit with ``on_entropy_failure: "queue"`` that hits a retryable seeding failure stores an
unseeded PENDING_ENTROPY job and self-invokes asynchronously with ``{"qmc_seed", "attempt"}``.
Each attempt backs off, then runs the same seeding as submit (§7.5 steps 2–5). Success moves the
job to QUEUED with a Qrypt-derived seed and enqueues it; failures retry up to
PENDING_MAX_ATTEMPTS, then end FAILED naming the cause. Every transition is conditional on
``status = PENDING_ENTROPY``, so the seeder and GET's expiry backstop can race safely.
"""

from __future__ import annotations

import json
import time

from qmc_api import aws, jobs, submit
from qmc_core import ddb, emf, logjson
from qmc_core.errors import RETRYABLE_ENTROPY_CODES, ErrorCode, QmcError
from qmc_core.params import is_job_id

PENDING = "PENDING_ENTROPY"
WORK_RESERVE_MS = 1000  # after the Qrypt budget: claim, two writes, enqueue


def _sleep(seconds: float) -> None:
    """The backoff sleep (module-level so tests can patch it)."""
    time.sleep(seconds)


def invoke_seeder(job_id: str, attempt: int) -> None:
    aws.lambda_().invoke(
        FunctionName=aws.env("AWS_LAMBDA_FUNCTION_NAME"),
        InvocationType="Event",
        Payload=json.dumps({"qmc_seed": job_id, "attempt": attempt}).encode(),
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
    room = (
        context.get_remaining_time_in_millis()
        - aws.env_int("QRYPT_DEADLINE_MS")
        - submit.LAMBDA_MARGIN_MS
        - WORK_RESERVE_MS
    )
    return max(0, min(wanted, room)) / 1000


def run(event, context) -> None:
    """The seeder entry point (an async self-invoke). Never raises: a Lambda error would trip
    the api-errors alarm, and async retries are off."""
    job_id = event.get("qmc_seed")
    try:
        _attempt(job_id, int(event["attempt"]), context)
    except Exception as exc:
        logjson.log("seeder_error", level="ERROR", error=type(exc).__name__, detail=str(exc))
        if is_job_id(job_id):
            try:
                fail_pending(aws.table(), job_id, ErrorCode.INTERNAL_ERROR)
            except Exception as fail_exc:
                logjson.log("pending_fail_failed", level="ERROR", error=type(fail_exc).__name__)


def _attempt(job_id, attempt: int, context) -> None:
    logjson.bind(job_id=job_id, attempt=attempt)
    if not is_job_id(job_id):
        logjson.log("seed_skipped", level="WARNING", reason="bad job id")
        return
    table = aws.table()
    job = jobs.read(table, job_id)
    if job is None or job["status"] != PENDING:
        logjson.log("seed_skipped", reason=job["status"] if job else "missing")
        return
    if time.time() > int(job["pending_until"]):
        fail_pending(table, job_id, job.get("last_entropy_error") or "PENDING_EXPIRED")
        return

    _sleep(backoff_s(attempt, context))
    try:
        seed, entropy = submit.seed_job(table, job_id, context)
    except QmcError as exc:
        _failed(table, job_id, attempt, exc.code)
        return

    try:
        table.update_item(
            **ddb.pending_to_queued(
                job_id,
                seed.hex,
                entropy.provenance(seed),
                time.time(),
                aws.env_int("JOB_DEADLINE_S"),
            )
        )
    except Exception as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        logjson.log("pending_to_queued_refused")  # GET's expiry backstop failed it first
        return
    logjson.log("entropy_seeded")
    try:
        submit.publish(table, jobs.read(table, job_id), joblist=False)
    except QmcError:
        return  # already marked FAILED and logged by publish
    emf.emit("JobsSubmitted", 1, job_id=job_id, pending_attempt=attempt)


def _failed(table, job_id: str, attempt: int, code: ErrorCode) -> None:
    logjson.log("entropy_attempt_failed", cause=code.name)
    if code not in RETRYABLE_ENTROPY_CODES:
        fail_pending(table, job_id, code)
        return
    try:
        table.update_item(**ddb.pending_attempt_failed(job_id, code.name))
    except Exception as exc:
        if not ddb.is_conditional_failure(exc):
            raise
        logjson.log("pending_attempt_refused", cause=code.name)
        return
    if attempt >= aws.env_int("PENDING_MAX_ATTEMPTS"):
        fail_pending(table, job_id, code)
        return
    try:
        invoke_seeder(job_id, attempt + 1)
    except Exception as exc:
        logjson.log("seeder_invoke_failed", level="ERROR", error=type(exc).__name__)
        fail_pending(table, job_id, code)
