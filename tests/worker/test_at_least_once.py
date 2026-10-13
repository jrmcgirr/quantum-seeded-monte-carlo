"""QMC-132: duplicates, crash between writes, finalize races. Interleavings are deterministic
(moto is not thread-safe, HANDOFF §4.1): one worker's steps run inside another's at a fixed point."""

from __future__ import annotations

from qmc_core import ddb
from tests.worker.conftest import (
    event,
    events,
    expected_result,
    job_item,
    metric_total,
    record,
    stdout_lines,
)


def assert_clean_complete(table, item, lines):
    job = job_item(table, item["job_id"])
    assert job["status"] == "COMPLETE"
    assert job["result"] == expected_result(item)
    assert job["done_shards"] == set(range(item["shards"]))
    assert metric_total(lines, "PathsSimulated") == item["paths"]  # duplicates never inflate it
    assert metric_total(lines, "JobsCompleted") == 1


def test_sequential_duplicates(worker, ctx, make_job, ddb_table, iam_guard, capsys):
    item, msgs = make_job()
    capsys.readouterr()
    with iam_guard("worker"):
        for m in msgs + msgs:
            assert worker.handle(event(record(m)), ctx) == {"batchItemFailures": []}
    lines = stdout_lines(capsys)
    assert len(events(lines, "duplicate_delivery")) == item["shards"]
    assert_clean_complete(ddb_table, item, lines)


def test_concurrent_duplicate_runs_between_put_and_mark_done(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    """Worker A writes shard 0; a duplicate B runs to completion; then A marks done and finalizes."""
    item, msgs = make_job(shards=2, paths=400)
    worker.handle(event(record(msgs[1])), ctx)
    capsys.readouterr()

    real_mark_done = ddb.mark_done
    nested = []

    def mark_done(job_id, i):
        if not nested:
            nested.append(None)  # before the call: the nested worker hits this hook too
            nested[0] = worker.handle(event(record(msgs[0])), ctx)
        return real_mark_done(job_id, i)

    monkeypatch.setattr(ddb, "mark_done", mark_done)
    with iam_guard("worker"):
        assert worker.handle(event(record(msgs[0])), ctx) == {"batchItemFailures": []}
    assert nested == [{"batchItemFailures": []}]

    lines = stdout_lines(capsys)
    assert len(events(lines, "duplicate_delivery")) == 1
    assert len(events(lines, "job_finalized")) == 1
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE" and job["result"] == expected_result(item)


def test_crash_between_put_and_mark_done_then_redelivery(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    item, msgs = make_job()
    capsys.readouterr()
    real_mark_done = ddb.mark_done

    def crash_once(job_id, i):
        monkeypatch.setattr(ddb, "mark_done", real_mark_done)
        raise RuntimeError("simulated crash after shard_put")

    with iam_guard("worker"):
        for m in msgs[1:]:
            worker.handle(event(record(m)), ctx)
        monkeypatch.setattr(ddb, "mark_done", crash_once)
        rec = record(msgs[0])
        assert worker.handle(event(rec), ctx) == {
            "batchItemFailures": [{"itemIdentifier": rec["messageId"]}]
        }
    assert job_item(ddb_table, item["job_id"])["status"] == "QUEUED"  # test read, outside the guard
    with iam_guard("worker"):
        assert worker.handle(event(record(msgs[0], attempt=2)), ctx) == {"batchItemFailures": []}

    lines = stdout_lines(capsys)
    assert len(events(lines, "duplicate_delivery")) == 1
    assert_clean_complete(ddb_table, item, lines)


def _defer_finalize(worker, monkeypatch):
    real = worker._finalize
    pending = []
    monkeypatch.setattr(worker, "_finalize", lambda job_id, job: pending.append((job_id, job)))
    return real, pending


def test_two_workers_finish_last_two_shards_together(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    """Both last shards are counted before either finalizes: exactly one finalize write."""
    item, msgs = make_job()
    with iam_guard("worker"):
        for m in msgs[:-2]:
            worker.handle(event(record(m)), ctx)
        capsys.readouterr()
        real, pending = _defer_finalize(worker, monkeypatch)
        for m in msgs[-2:]:
            worker.handle(event(record(m)), ctx)
        for job_id, job in pending:
            real(job_id, job)

    lines = stdout_lines(capsys)
    assert len(events(lines, "job_finalized")) == 1
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE" and job["result"] == expected_result(item)


def test_duplicate_last_shard_both_finalize_one_wins(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch, capsys
):
    """Both deliveries of the last shard see every shard done; the second finalize is refused."""
    item, msgs = make_job()
    with iam_guard("worker"):
        for m in msgs[:-1]:
            worker.handle(event(record(m)), ctx)
        capsys.readouterr()
        real, pending = _defer_finalize(worker, monkeypatch)
        worker.handle(event(record(msgs[-1])), ctx)
        worker.handle(event(record(msgs[-1])), ctx)
        assert len(pending) == 2
        for job_id, job in pending:
            real(job_id, job)

    lines = stdout_lines(capsys)
    assert len(events(lines, "job_finalized")) == 1
    assert len(events(lines, "finalize_refused")) == 1
    assert metric_total(lines, "JobsCompleted") == 1
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE" and job["result"] == expected_result(item)
