"""GET /jobs/{id} with lazy finalize and the deadline reaper (HANDOFF §5.4, §7.7), and GET /jobs."""

from __future__ import annotations

import time

from qmc_api import aws, pending
from qmc_core import bs, codec, cost, ddb, emf, logjson, replay
from qmc_core.aggregate import Result, aggregate, decide
from qmc_core.errors import ErrorCode, QmcError
from qmc_core.params import is_job_id

LIST_LIMIT = 50
TERMINAL = ("COMPLETE", "PARTIAL", "FAILED")


def _not_found(job_id: str) -> QmcError:
    return QmcError(ErrorCode.JOB_NOT_FOUND, f"No job with id {job_id!r}.")


def read(table, job_id: str) -> dict | None:
    return table.get_item(**ddb.job_get(job_id)).get("Item")


def _shards(table, job_id: str) -> list[dict]:
    kwargs, items = ddb.shards_query(job_id), []
    while True:
        page = table.query(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs = {**kwargs, "ExclusiveStartKey": page["LastEvaluatedKey"]}


def settled_job(table, job_id: str) -> dict:
    """The job item read consistently, after finalizing or reaping it if it is due.

    Every write is conditional on ``status = QUEUED``; a refused write means someone else
    (a worker, another GET) moved first, so re-read and re-decide once.
    """
    if not is_job_id(job_id):
        raise _not_found(job_id)
    job = read(table, job_id)
    if job is None:
        raise _not_found(job_id)
    if job["status"] == pending.PENDING and time.time() > int(job["pending_until"]):
        # The seeder's async event was lost or is stuck: the expiry backstop (contract change 008).
        pending.fail_pending(table, job_id, job.get("last_entropy_error") or "PENDING_EXPIRED")
        return read(table, job_id)
    for _ in range(2):
        now = time.time()
        d = decide(job, now_epoch=now)
        if d.action == "none":
            return job
        shards = _shards(table, job_id)
        try:
            if d.action == "complete":
                table.update_item(**ddb.finalize_complete(job_id, aggregate(shards), now))
                emf.emit("JobsCompleted", 1, job_id=job_id, by="api")
            else:
                partial = aggregate(shards, only=d.done)
                table.update_item(
                    **ddb.finalize_partial(job_id, partial, d.add_failed, d.n_done_seen, now)
                )
                emf.emit("JobsPartial", 1, job_id=job_id, by="api", reason=d.reason)
            logjson.log("lazy_finalize", action=d.action, reason=d.reason)
        except Exception as exc:
            if not ddb.is_conditional_failure(exc):
                raise
            logjson.log("lazy_finalize_refused", action=d.action, reason=d.reason)
        job = read(table, job_id)
    return job


def get_job(event, context, job_id: str) -> tuple[int, dict]:
    table = aws.table()
    job = settled_job(table, job_id)
    shards = _shards(table, job_id) if job["status"] in TERMINAL else []
    return 200, view(table, job, shards)


def _result_view(item) -> dict | None:
    return Result.from_item(item).to_view() if item else None


def view(table, job: dict, shards: list[dict]) -> dict:
    """The §5.4 job view."""
    done = codec.ns_to_set(job.get("done_shards"))
    failed = codec.ns_to_set(job.get("failed_shards")) - done
    status = job["status"]
    if status == "QUEUED" and (done or failed):
        status = "RUNNING"
    params = codec.params_from_repr(job["params"])
    result = _result_view(job.get("result"))
    partial = _result_view(job.get("partial_result"))
    shown = result or partial
    return {
        "job_id": job["job_id"],
        "status": status,
        "created_at": job["created_at"],
        "completed_at": job.get("completed_at"),
        "params": params,
        "paths": job["paths"],
        "shards": job["shards"],
        "variance_reduction": job["variance_reduction"],
        "chunk_size": job["chunk_size"],
        "progress": {"done": len(done), "failed": len(failed), "total": job["shards"]},
        "master_seed_hex": job.get("master_seed_hex"),  # absent while PENDING_ENTROPY
        "seed_provenance": job.get("seed_provenance"),
        "result": result,
        "reference": bs.reference(params, shown["estimate"], shown["std_error"]) if shown else None,
        "failed_shards": sorted(failed),
        "partial_result": partial,
        "replay_of": job.get("replay_of"),
        "replay_check": _replay_check(table, job, shards),
        "fingerprint": replay.job_fingerprint(shards) if shards else None,
        "cost_estimate": cost.estimate(job, shards) if shards else None,
        "entropy_attempts": job.get("entropy_attempts"),
        "last_entropy_error": job.get("last_entropy_error"),
        "pending_until": job.get("pending_until"),
        "fail_reason": job.get("fail_reason"),
    }


def _replay_check(table, job: dict, shards: list[dict]) -> dict | None:
    original_id = job.get("replay_of")
    if not original_id or job["status"] not in ("COMPLETE", "PARTIAL"):
        return None
    original = read(table, original_id)
    if original is None:  # expired by TTL
        return None
    return replay.compare(original, _shards(table, original_id), job, shards)


def list_jobs(event, context) -> tuple[int, dict]:
    """Newest 50 from JOBLIST plus one BatchGetItem for current statuses. Never a Scan."""
    table = aws.table()
    entries = table.query(**ddb.joblist_query(LIST_LIMIT))["Items"]
    statuses: dict[str, str] = {}
    if entries:
        resp = aws.dynamodb().batch_get_item(
            **ddb.job_statuses(table.name, [e["job_id"] for e in entries])
        )
        # ponytail: UnprocessedKeys fall back to status_at_submit; retry them if lists look stale.
        statuses = {i["job_id"]: i["status"] for i in resp["Responses"].get(table.name, [])}
    return 200, {
        "jobs": [
            {
                "job_id": e["job_id"],
                "status": statuses.get(e["job_id"], e["status_at_submit"]),
                "created_at": e["sk"].split("#", 1)[0],
                "paths": e["paths"],
                "shards": e["shards"],
                "replay_of": e.get("replay_of"),
            }
            for e in entries
        ]
    }
