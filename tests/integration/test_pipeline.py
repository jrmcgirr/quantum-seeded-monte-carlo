"""Submit → worker → finalize → replay, in process (QMC-141; HANDOFF §11 rows Core 3–6,
duplicate delivery, worker failure → PARTIAL, reaper)."""

from __future__ import annotations

import random

import pytest
from botocore.client import BaseClient

from .conftest import SMALL_JOB, Crash, expected_estimate_hex, shard_index


def _assert_complete_and_exact(app, job_id):
    view = app.get(job_id)
    assert view["status"] == "COMPLETE", view
    assert view["progress"] == {"done": 4, "failed": 0, "total": 4}
    assert view["result"]["n"] == SMALL_JOB["paths"]
    assert view["result"]["estimate_hex"] == expected_estimate_hex(view["master_seed_hex"])
    assert view["reference"]["within_3se"] in (True, False)  # present; 20k paths prove nothing
    return view


def test_happy_path_complete(app):
    job_id = app.submit()
    records = app.receive_all()
    app.run_all(records[:2])
    assert app.get(job_id)["status"] == "RUNNING"
    app.run_all(records[2:])
    _assert_complete_and_exact(app, job_id)


def test_out_of_order_delivery(app):
    job_id = app.submit()
    records = app.receive_all()
    random.Random(7).shuffle(records)
    app.run_all(sorted(records, key=shard_index, reverse=True))
    _assert_complete_and_exact(app, job_id)


def test_every_message_delivered_twice(app):
    job_id = app.submit()
    records = app.receive_all()
    for rec in records:
        assert app.deliver(rec, attempt=1)
        assert app.deliver(rec, attempt=2)
        app.ack(rec)
    view = _assert_complete_and_exact(app, job_id)
    assert sum(int(s["n"]) for s in app.shard_items(job_id)) == SMALL_JOB["paths"]

    # A redelivery after COMPLETE changes nothing.
    before = app.job_item(job_id)
    assert app.deliver(records[0], attempt=3)
    assert app.job_item(job_id) == before
    assert app.get(job_id)["result"] == view["result"]


def test_crash_between_shard_put_and_mark_done(app, monkeypatch):
    job_id = app.submit()
    records = app.receive_all()
    original = BaseClient._make_api_call
    crashed = []

    def crash_once_on_mark_done(self, op, params):
        names = params.get("ExpressionAttributeNames", {}).values()
        if op == "UpdateItem" and "done_shards" in names and not crashed:
            crashed.append(op)
            raise Crash
        return original(self, op, params)

    monkeypatch.setattr(BaseClient, "_make_api_call", crash_once_on_mark_done)
    with pytest.raises(Crash):
        app.deliver(records[0], attempt=1)
    assert len(app.shard_items(job_id)) == 1  # the put landed, the count did not
    assert "done_shards" not in app.job_item(job_id)

    assert app.deliver(records[0], attempt=2)  # redelivery after the visibility timeout
    app.ack(records[0])
    app.run_all(records[1:])
    _assert_complete_and_exact(app, job_id)


def test_forced_failure_ends_partial(app):
    job_id = app.submit({**SMALL_JOB, "fault_injection": {"fail_shards": [1]}})
    records = app.receive_all()
    bad = next(r for r in records if shard_index(r) == 1)
    app.run_all([r for r in records if r is not bad])
    for attempt in (1, 2, 3):
        assert not app.deliver(bad, attempt=attempt)  # reported failed every time → DLQ

    view = app.get(job_id)
    assert view["status"] == "PARTIAL"
    assert view["failed_shards"] == [1]
    assert view["result"] is None
    assert view["partial_result"]["n"] == SMALL_JOB["paths"] - 5_000


def test_reaper_marks_missing_shard_partial_and_partial_is_final(app):
    job_id = app.submit()
    records = sorted(app.receive_all(), key=shard_index)
    app.run_all(records[:3])
    app.ack(records[3])  # message lost
    app.table.update_item(
        Key={"pk": f"JOB#{job_id}", "sk": "META"},
        UpdateExpression="SET deadline_at = :d",
        ExpressionAttributeValues={":d": 1},
    )
    view = app.get(job_id)
    assert view["status"] == "PARTIAL"
    assert view["failed_shards"] == [3]

    app.deliver(records[3], attempt=1)  # a late shard cannot change a final status
    assert app.get(job_id)["status"] == "PARTIAL"


def test_replay_is_bit_identical(app):
    job_id = app.submit()
    app.run_all(app.receive_all())
    original = _assert_complete_and_exact(app, job_id)

    calls = app.source.calls
    r = app.call("POST", f"/jobs/{job_id}/replay")
    assert r.status == 202, r.body
    assert app.source.calls == calls  # a replay never calls Qrypt
    replay_id = r.body["job_id"]
    assert replay_id != job_id and r.body["replay_of"] == job_id
    app.run_all(app.receive_all())

    view = app.get(replay_id)
    assert view["status"] == "COMPLETE"
    assert view["master_seed_hex"] == original["master_seed_hex"]
    assert view["seed_provenance"] == {"source": "replay", "replay_of": job_id}
    assert view["result"]["estimate_hex"] == original["result"]["estimate_hex"]
    assert view["replay_check"]["bit_identical"] is True
    assert view["replay_check"]["shard_diffs"] == []


def test_replay_rules(app):
    assert app.call("POST", f"/jobs/{'0' * 32}/replay").status == 404
    queued = app.submit()
    r = app.call("POST", f"/jobs/{queued}/replay")
    assert (r.status, r.body["error"]["code"]) == (409, "JOB_NOT_REPLAYABLE")
