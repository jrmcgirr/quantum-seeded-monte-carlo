"""Stretch: antithetic shards (QMC-108) and PENDING_ENTROPY jobs (QMC-128, contract change 008)."""

from __future__ import annotations

import time

from qmc_core import codec, ddb, messages, params, sim
from tests.worker.conftest import (
    REF_PARAMS,
    event,
    events,
    expected_result,
    job_item,
    record,
    stdout_lines,
)


def test_antithetic_shards_store_pairs(worker, ctx, make_job, ddb_table, iam_guard):
    item, msgs = make_job(paths=2000, shards=10, variance_reduction="antithetic")
    assert all(m.variance_reduction == "antithetic" for m in msgs)

    with iam_guard("worker"):
        for m in msgs:
            assert worker.handle(event(record(m)), ctx) == {"batchItemFailures": []}

    shard = ddb_table.get_item(Key=ddb.shard_key(item["job_id"], 0))["Item"]
    assert shard["n"] == msgs[0].shard_paths // 2  # n counts pairs
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE"
    assert job["result"]["n"] == item["paths"] // 2
    assert job["result"] == expected_result(item)


def _pending_job(table) -> dict:
    job_id = "0123456789abcdef0123456789abcdef"
    item = ddb.new_pending_job_item(
        job_id=job_id,
        params=codec.params_to_repr(REF_PARAMS),
        paths=2000,
        shards=10,
        paths_per_shard=params.split_paths(2000, 10),
        chunk_size=sim.CHUNK_SIZE,
        variance_reduction="none",
        algo_version=sim.ALGO_VERSION,
        now_epoch=time.time(),
        max_age_s=120,
        last_error="QRYPT_TIMEOUT",
    )
    table.put_item(**ddb.job_put(item))
    return item


def test_pending_job_has_no_seed_or_deadline_until_seeded_then_completes(
    worker, ctx, ddb_table, iam_guard
):
    """No message can exist before seeding (no seed to put in it); deadline_at is set at seeding."""
    item = _pending_job(ddb_table)
    pending = job_item(ddb_table, item["job_id"])
    assert pending["status"] == "PENDING_ENTROPY"
    assert "master_seed_hex" not in pending and "deadline_at" not in pending

    now = time.time()
    seed_hex = "ab" * 32
    ddb_table.update_item(
        **ddb.pending_to_queued(item["job_id"], seed_hex, {"source": "test"}, now, 900)
    )
    queued = job_item(ddb_table, item["job_id"])
    assert queued["status"] == "QUEUED"
    assert int(queued["deadline_at"]) == int(now + 900)

    with iam_guard("worker"):
        for m in messages.for_job(queued):
            assert worker.handle(event(record(m)), ctx) == {"batchItemFailures": []}
    job = job_item(ddb_table, item["job_id"])
    assert job["status"] == "COMPLETE" and job["result"] == expected_result(queued)


def test_stray_message_never_finalizes_a_pending_job(worker, ctx, ddb_table, iam_guard, capsys):
    """Defence in depth: even if a shard message reached a PENDING job, decide() says none."""
    item = _pending_job(ddb_table)
    msgs = messages.for_job({**item, "master_seed_hex": "cd" * 32})
    capsys.readouterr()

    with iam_guard("worker"):
        for m in msgs:
            worker.handle(event(record(m)), ctx)

    assert job_item(ddb_table, item["job_id"])["status"] == "PENDING_ENTROPY"
    assert not events(stdout_lines(capsys), "job_finalized")
