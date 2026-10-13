"""Edge paths: algo_version refusal (I5), non-conditional DynamoDB errors, the job vanishing,
shard query pagination, and the failure path itself failing."""

from __future__ import annotations

import dataclasses

import pytest
from botocore.exceptions import ClientError

from qmc_core import ddb, sim
from tests.worker.conftest import (
    event,
    events,
    expected_result,
    job_item,
    record,
    stdout_lines,
)


def failed(rec) -> dict:
    return {"batchItemFailures": [{"itemIdentifier": rec["messageId"]}]}


def throttle(monkeypatch, worker, builder: str) -> None:
    """Make the table call built by ``ddb.<builder>`` raise a throttle (not a conditional failure)."""
    real = getattr(ddb, builder)
    monkeypatch.setattr(ddb, builder, lambda *a, **k: {**real(*a, **k), "_throttle": True})
    table = worker._table()
    for method in ("put_item", "update_item"):
        orig = getattr(table, method)

        def wrapped(_orig=orig, **kw):
            if kw.pop("_throttle", False):
                raise ClientError(
                    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
                    "Throttled",
                )
            return _orig(**kw)

        monkeypatch.setattr(table, method, wrapped)


@pytest.mark.parametrize("attempt", [1, 3])
def test_other_algo_version_is_refused_never_computed(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys, attempt
):
    """I5: a message for another simulation version is never computed; it fails like any error."""
    item, msgs = make_job()
    sims, visibility = [], []
    monkeypatch.setattr(sim, "simulate_shard", lambda *a, **k: sims.append(a))
    sqs = worker._sqs()
    real_vis = sqs.change_message_visibility

    def spy(**kw):
        visibility.append(kw["VisibilityTimeout"])
        return real_vis(**kw)

    monkeypatch.setattr(sqs, "change_message_visibility", spy)
    rec = record(dataclasses.replace(msgs[0], algo_version="qmc-sim/v0"), attempt=attempt)
    capsys.readouterr()

    with iam_guard("worker"):
        assert worker.handle(event(rec), ctx) == failed(rec)

    assert sims == []
    assert visibility == [5]
    assert "Item" not in ddb_table.get_item(Key=ddb.shard_key(item["job_id"], 0))
    job = job_item(ddb_table, item["job_id"])
    assert "done_shards" not in job
    if attempt == 3:
        assert job["failed_shards"] == {0}
    else:
        assert "failed_shards" not in job
    assert any("algo_version" in e["error"] for e in events(stdout_lines(capsys), "shard_error"))


@pytest.mark.parametrize("builder", ["shard_put", "mark_done", "finalize_complete"])
def test_dynamodb_error_reports_failure_then_redelivery_completes(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, builder
):
    """A throttle is not a duplicate or a lost race: report the item, and the retry repairs it."""
    item, msgs = make_job()
    with iam_guard("worker"):
        for m in msgs[:-1]:
            worker.handle(event(record(m)), ctx)
        with monkeypatch.context() as m:
            throttle(m, worker, builder)
            rec = record(msgs[-1])
            assert worker.handle(event(rec), ctx) == failed(rec)
    assert job_item(ddb_table, item["job_id"])["status"] == "QUEUED"

    with iam_guard("worker"):
        assert worker.handle(event(record(msgs[-1], attempt=2)), ctx) == {"batchItemFailures": []}
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE" and job["result"] == expected_result(item)


def test_failure_path_failing_is_still_reported(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    """mark_failed throttled on the last attempt: the handler never raises, the item is reported."""
    monkeypatch.setenv("FAULT_INJECTION_ENABLED", "true")
    item, msgs = make_job(fail_shards=[0])
    throttle(monkeypatch, worker, "mark_failed")
    rec = record(msgs[0], attempt=3)
    capsys.readouterr()

    with iam_guard("worker"):
        assert worker.handle(event(rec), ctx) == failed(rec)

    assert events(stdout_lines(capsys), "unhandled_error")
    assert "failed_shards" not in job_item(ddb_table, item["job_id"])


def test_last_attempt_on_vanished_job_writes_nothing(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    monkeypatch.setenv("FAULT_INJECTION_ENABLED", "true")
    item, msgs = make_job(fail_shards=[0])
    ddb_table.delete_item(Key=ddb.job_key(item["job_id"]))
    rec = record(msgs[0], attempt=3)
    capsys.readouterr()

    with iam_guard("worker"):
        assert worker.handle(event(rec), ctx) == failed(rec)

    refused = events(stdout_lines(capsys), "mark_failed_refused")
    assert refused and refused[0]["job_exists"] is False
    assert "Item" not in ddb_table.get_item(Key=ddb.job_key(item["job_id"]))


def test_job_vanishing_before_finalize_writes_nothing(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    item, msgs = make_job()
    real = worker._finalize
    pending = []
    monkeypatch.setattr(worker, "_finalize", lambda job_id, job: pending.append((job_id, job)))
    with iam_guard("worker"):
        for m in msgs:
            worker.handle(event(record(m)), ctx)
    ddb_table.delete_item(Key=ddb.job_key(item["job_id"]))
    capsys.readouterr()

    with iam_guard("worker"):
        real(*pending[-1])

    lines = stdout_lines(capsys)
    assert events(lines, "finalize_refused") and not events(lines, "job_finalized")
    assert "Item" not in ddb_table.get_item(Key=ddb.job_key(item["job_id"]))


def test_finalize_follows_shard_query_pages(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch
):
    real = ddb.shards_query
    monkeypatch.setattr(ddb, "shards_query", lambda job_id: {**real(job_id), "Limit": 3})
    item, msgs = make_job()
    with iam_guard("worker"):
        for m in msgs:
            worker.handle(event(record(m)), ctx)
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE" and job["result"] == expected_result(item)
