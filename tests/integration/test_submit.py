"""Core 1 and first run (HANDOFF §11): POST /jobs records a QUEUED job with its seed and params."""

from __future__ import annotations

from .conftest import SMALL_JOB, shard_index


def test_fresh_deployment_lists_no_jobs(app):
    r = app.call("GET", "/jobs")
    assert (r.status, r.body) == (200, {"jobs": []})


def test_unknown_job_is_404_naming_the_id(app):
    job_id = "0" * 32
    r = app.call("GET", f"/jobs/{job_id}")
    assert r.status == 404
    assert r.body["error"]["code"] == "JOB_NOT_FOUND"
    assert job_id in r.body["error"]["message"]


def test_submit_records_queued_job_and_one_message_per_shard(app):
    r = app.call("POST", "/jobs", SMALL_JOB)
    assert r.status == 202, r.body
    job_id = r.body["job_id"]
    assert r.body["status"] == "QUEUED"
    assert len(r.body["master_seed_hex"]) == 64

    item = app.job_item(job_id)
    assert item["status"] == "QUEUED"
    assert item["master_seed_hex"] == r.body["master_seed_hex"]
    assert item["params"] == {k: repr(float(SMALL_JOB[k])) for k in ("S0", "K", "r", "sigma", "T")}
    assert item["seed_provenance"]["source"] == app.source.source == "fake"

    records = app.receive_all()
    assert sorted(shard_index(x) for x in records) == [0, 1, 2, 3]

    listed = app.call("GET", "/jobs").body["jobs"]
    assert [j["job_id"] for j in listed] == [job_id]


def test_invalid_request_writes_nothing(app):
    r = app.call("POST", "/jobs", {**SMALL_JOB, "sigma": 0, "paths": 3, "shards": 4})
    assert r.status == 400
    assert r.body["error"]["code"] == "VALIDATION_ERROR"
    assert {d["field"] for d in r.body["error"]["details"]} >= {"sigma", "shards"}
    assert app.table.scan()["Items"] == []
    assert app.receive_all() == []


def test_qrypt_failure_rejects_and_writes_nothing(app):
    """I1 fail closed: a Qrypt 401 names the cause, and no job, list entry or message exists.
    (Only the limiter slot may be written: HANDOFF §7.5 step 2.)"""
    import qmc_api.qrypt as qrypt
    from qmc_core.errors import ErrorCode

    failing = qrypt.FakeEntropySource(
        seed=b"qmc-fail-closed", failures=[ErrorCode.QRYPT_AUTH_FAILED]
    )
    qrypt.set_entropy_source(failing)
    r = app.call("POST", "/jobs", SMALL_JOB)
    assert (r.status, r.body["error"]["code"]) == (503, "QRYPT_AUTH_FAILED")
    assert [i["pk"] for i in app.table.scan()["Items"] if not i["pk"].startswith("RL#")] == []
    assert app.receive_all() == []
    assert failing.calls == 1  # 401 is never retried
