"""QMC-123 (lazy finalize, reaper, reference) and QMC-124 (replay, replay_check)."""

from __future__ import annotations

import time

import pytest

from qmc_core import ddb
from qmc_core.aggregate import ShardResult
from tests.api.conftest import REF

FP = {
    "python": "3.13.0",
    "numpy": "2.5.4",
    "machine": "x86_64",
    "exp_target": "AVX2",
    "algo_version": "qmc-sim/v1",
    "bitgen": "PCG64DXSM",
    "chunk_size": 131072,
    "cpu_model": "test cpu",
}


def land(table, job_id: str, i: int, s: float | None = None) -> None:
    """What a worker does for shard i: shard put, then mark done."""
    s = 2600.0 + i if s is None else s
    result = ShardResult(n=250, sum=s, sum_sq=s * 120.5)
    table.put_item(
        **ddb.shard_put(
            job_id,
            i,
            result,
            duration_ms=50,
            memory_mb=1024,
            attempt=1,
            fingerprint=FP,
            now_epoch=time.time(),
        )
    )
    table.update_item(**ddb.mark_done(job_id, i))


def expire(table, job_id: str) -> None:
    table.update_item(
        Key=ddb.job_key(job_id),
        UpdateExpression="SET deadline_at = :one",
        ExpressionAttributeValues={":one": 1},
    )


def submit(api) -> str:
    status, _, body = api.call("POST", "/jobs", REF)
    assert status == 202, body
    return body["job_id"]


def get(api, job_id):
    status, _, body = api.call("GET", f"/jobs/{job_id}")
    assert status == 200, body
    return body


def writes_during_get(api, iam_guard, job_id) -> int:
    from qmc_api import handler
    from tests.api.conftest import Ctx, event

    with iam_guard("api") as calls:
        handler.handle(event("GET", f"/jobs/{job_id}"), Ctx())
    return sum(op in ("PutItem", "UpdateItem") for _, op in calls)


def test_queued_then_running(api):
    job_id = submit(api)
    assert get(api, job_id)["status"] == "QUEUED"
    land(api.table, job_id, 0)
    view = get(api, job_id)
    assert view["status"] == "RUNNING"
    assert view["progress"] == {"done": 1, "failed": 0, "total": 4}
    assert view["result"] is None and view["fingerprint"] is None


def test_get_finalizes_complete_once(api, iam_guard):
    job_id = submit(api)
    for i in range(4):
        land(api.table, job_id, i)
    assert writes_during_get(api, iam_guard, job_id) == 1
    assert writes_during_get(api, iam_guard, job_id) == 0  # a second GET writes nothing
    view = get(api, job_id)
    assert view["status"] == "COMPLETE" and view["completed_at"]
    assert view["result"]["n"] == 1000 and view["result"]["estimate_hex"].startswith("0x")
    assert view["reference"]["black_scholes"] == pytest.approx(10.450583572185565, abs=1e-12)
    assert set(view["reference"]) == {"black_scholes", "z_score", "within_3se"}
    assert view["failed_shards"] == [] and view["partial_result"] is None
    assert view["fingerprint"]["machine"] == "x86_64"
    assert view["params"] == {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}


def test_reaper_partials_past_deadline(api):
    job_id = submit(api)
    land(api.table, job_id, 0)
    land(api.table, job_id, 1)
    assert get(api, job_id)["status"] == "RUNNING"  # not due yet
    expire(api.table, job_id)
    view = get(api, job_id)
    assert view["status"] == "PARTIAL"
    assert view["failed_shards"] == [2, 3]
    assert view["result"] is None and view["partial_result"]["n"] == 500


def test_reaper_with_no_done_shard(api):
    job_id = submit(api)
    expire(api.table, job_id)
    view = get(api, job_id)
    assert view["status"] == "PARTIAL" and view["failed_shards"] == [0, 1, 2, 3]
    assert view["partial_result"] is None


@pytest.mark.parametrize("worker_finalizes", [False, True])
def test_worker_completes_last_shard_between_read_and_partial_write(
    api, monkeypatch, worker_finalizes
):
    """GET reads 3/4 done past the deadline; shard 3 lands before GET's PARTIAL write."""
    from qmc_api import jobs
    from qmc_core.aggregate import aggregate

    job_id = submit(api)
    for i in range(3):
        land(api.table, job_id, i)
    expire(api.table, job_id)

    real_decide, raced = jobs.decide, []

    def racing_decide(job, now_epoch=None):
        d = real_decide(job, now_epoch=now_epoch)
        if d.action == "partial" and not raced:
            raced.append(1)
            land(api.table, job_id, 3)
            if worker_finalizes:
                shards = api.table.query(**ddb.shards_query(job_id))["Items"]
                api.table.update_item(
                    **ddb.finalize_complete(job_id, aggregate(shards), time.time())
                )
        return d

    monkeypatch.setattr(jobs, "decide", racing_decide)
    view = get(api, job_id)
    assert raced
    assert view["status"] == "COMPLETE"
    assert view["failed_shards"] == [] and view["result"]["n"] == 1000


def test_replay_is_bit_identical_without_qrypt(api, iam_guard):
    original = submit(api)
    for i in range(4):
        land(api.table, original, i)
    assert get(api, original)["status"] == "COMPLETE"
    calls, [window] = api.fake.calls, api.items("RL#")
    api.messages()  # drain the original's

    status, _, body = api.call("POST", f"/jobs/{original}/replay")
    assert status == 202, body
    replay_id = body["job_id"]
    assert body["replay_of"] == original and replay_id != original
    assert api.fake.calls == calls  # no Qrypt call
    assert api.items("RL#") == [window]  # no limiter slot
    assert len(api.messages()) == 4

    view = get(api, replay_id)
    assert view["seed_provenance"] == {"source": "replay", "replay_of": original}
    assert view["master_seed_hex"] == get(api, original)["master_seed_hex"]
    assert view["replay_check"] is None  # not terminal yet

    for i in range(4):
        land(api.table, replay_id, i)
    view = get(api, replay_id)
    assert view["status"] == "COMPLETE"
    assert view["replay_check"]["bit_identical"] is True
    assert view["replay_check"]["shard_diffs"] == []


def test_replay_check_reports_a_diff(api):
    original = submit(api)
    for i in range(4):
        land(api.table, original, i)
    get(api, original)
    replay_id = api.call("POST", f"/jobs/{original}/replay")[2]["job_id"]
    for i in range(4):
        land(api.table, replay_id, i, s=2600.0 + i + (1e-9 if i == 2 else 0))
    check = get(api, replay_id)["replay_check"]
    assert check["bit_identical"] is False
    assert {d["shard_index"] for d in check["shard_diffs"]} == {2}


def test_replay_of_queued_is_409_and_unknown_is_404(api):
    job_id = submit(api)
    status, _, body = api.call("POST", f"/jobs/{job_id}/replay")
    assert (status, body["error"]["code"]) == (409, "JOB_NOT_REPLAYABLE")
    status, _, body = api.call("POST", f"/jobs/{'f' * 32}/replay")
    assert (status, body["error"]["code"]) == (404, "JOB_NOT_FOUND")


def test_replay_does_not_copy_fault_injection(api, monkeypatch):
    monkeypatch.setenv("FAULT_INJECTION_ENABLED", "true")
    _, _, body = api.call("POST", "/jobs", {**REF, "fault_injection": {"fail_shards": [0]}})
    original = body["job_id"]
    for i in range(4):
        land(api.table, original, i)
    get(api, original)
    api.messages()
    replay_id = api.call("POST", f"/jobs/{original}/replay")[2]["job_id"]
    [job] = [j for j in api.items("JOB#") if j["job_id"] == replay_id and j["sk"] == "META"]
    assert "fault_injection" not in job
    assert not any(m["fail"] for m in api.messages())
