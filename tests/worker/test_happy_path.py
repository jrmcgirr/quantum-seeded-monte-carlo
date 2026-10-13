"""QMC-130: decode → simulate → idempotent shard put → mark done → finalize; init self-check."""

from __future__ import annotations

import importlib
import random

import pytest

from qmc_core import ddb, fingerprint
from tests.worker.conftest import (
    event,
    events,
    expected_result,
    job_item,
    metric_total,
    record,
    stdout_lines,
)


def test_shards_in_random_order_complete_with_direct_hex(
    worker, ctx, make_job, ddb_table, iam_guard, capsys
):
    item, msgs = make_job()
    random.Random(7).shuffle(msgs)
    capsys.readouterr()

    with iam_guard("worker"):
        for m in msgs:
            assert worker.handle(event(record(m)), ctx) == {"batchItemFailures": []}

    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE"
    assert job["result"] == expected_result(item)
    lines = stdout_lines(capsys)
    assert metric_total(lines, "PathsSimulated") == item["paths"]
    assert metric_total(lines, "JobsCompleted") == 1
    assert len(events(lines, "shard_written")) == item["shards"]

    shard = ddb_table.get_item(Key=ddb.shard_key(item["job_id"], 0))["Item"]
    assert shard["memory_mb"] == 1024 and shard["attempt"] == 1
    assert shard["fingerprint"]["exp_target"] == worker.EXP_TARGET
    assert "cpu_model" in shard["fingerprint"]


def test_not_all_shards_leaves_job_queued(worker, ctx, make_job, ddb_table, iam_guard):
    item, msgs = make_job()
    with iam_guard("worker"):
        for m in msgs[:-1]:
            worker.handle(event(record(m)), ctx)
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "QUEUED" and "result" not in job
    assert len(job["done_shards"]) == item["shards"] - 1


def test_orphan_shard_is_processed_not_failed(worker, ctx, make_job, ddb_table, iam_guard, capsys):
    item, msgs = make_job()
    ddb_table.delete_item(Key=ddb.job_key(item["job_id"]))
    capsys.readouterr()
    with iam_guard("worker"):
        assert worker.handle(event(record(msgs[0])), ctx) == {"batchItemFailures": []}
    assert events(stdout_lines(capsys), "orphan_shard")


def test_init_self_check_raises_before_any_message(worker, monkeypatch):
    def wrong_dispatch():
        raise fingerprint.DispatchError("exp dispatched to AVX512_SKX")

    with monkeypatch.context() as m:
        m.setattr(fingerprint, "assert_reproducible_dispatch", wrong_dispatch)
        # Lambda runs init once per sandbox; an init error fails every invocation on it.
        with pytest.raises(fingerprint.DispatchError):
            importlib.reload(worker)
    importlib.reload(worker)  # restore a healthy module for the remaining tests


def test_billing_fields_charge_init_once_and_time_the_invocation(
    worker, ctx, make_job, ddb_table, iam_guard, monkeypatch
):
    """Contract change 009: init_ms on the sandbox's first shard only; invocation_ms covers more
    than the simulation (duration_ms)."""
    monkeypatch.setattr(worker, "INIT_MS", 123)
    monkeypatch.setattr(worker, "_init_unbilled", True)
    item, msgs = make_job(shards=2)

    with iam_guard("worker"):
        for m in msgs:
            worker.handle(event(record(m)), ctx)

    shards = [ddb_table.get_item(Key=ddb.shard_key(item["job_id"], i))["Item"] for i in range(2)]
    assert [s["init_ms"] for s in shards] == [123, 0]
    for s in shards:
        assert s["invocation_ms"] >= s["duration_ms"]
