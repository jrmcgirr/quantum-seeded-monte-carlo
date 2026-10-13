"""QMC-126 per-job cost attribution (HANDOFF §5.4, §7.10): cost_estimate must agree with the shard
items actually stored, per the exact formula in the qmc_core.cost docstring (MR !37)."""

from __future__ import annotations

import math

from qmc_core import cost as c

from .conftest import SMALL_JOB, shard_index

KEYS = {
    "lambda_gb_seconds",
    "lambda_requests",
    "sqs_requests",
    "dynamodb_write_units",
    "usd_at_list_price",
    "usd_after_free_tier",
}


def test_cost_estimate_matches_stored_shards(app):
    job_id = app.submit()
    app.run_all(app.receive_all())
    view = app.get(job_id)
    assert view["status"] == "COMPLETE"
    cost = view["cost_estimate"]
    assert cost is not None and set(cost) == KEYS, cost

    shards = app.shard_items(job_id)
    n = SMALL_JOB["shards"]
    assert len(shards) == n
    worker_gb_s = sum(int(s["duration_ms"]) / 1000 * int(s["memory_mb"]) / 1024 for s in shards)

    gb_s = worker_gb_s + 0.125  # API: 512 MB × 0.25 s, × (1 + polls); the view passes polls = 0
    assert abs(cost["lambda_gb_seconds"] - gb_s) < 1e-12, (cost["lambda_gb_seconds"], gb_s)
    assert cost["lambda_requests"] == 1 + n
    assert cost["sqs_requests"] == math.ceil(n / 10) + 2 * n  # sends + receives + deletes
    assert cost["dynamodb_write_units"] == 5 + 2 * n + 1
    usd = math.fsum(
        (
            gb_s * c.LAMBDA_USD_PER_GB_S,
            (1 + n) * c.LAMBDA_USD_PER_REQUEST,
            (math.ceil(n / 10) + 2 * n) * c.SQS_USD_PER_REQUEST,
            (6 + 2 * n) * c.DYNAMODB_USD_PER_WRITE,
        )
    )
    assert abs(cost["usd_at_list_price"] - usd) < 1e-15
    assert cost["usd_after_free_tier"] == 0.0


def test_partial_job_has_a_cost_over_its_done_shards(app):
    job_id = app.submit({**SMALL_JOB, "fault_injection": {"fail_shards": [1]}})
    records = app.receive_all()
    bad = next(r for r in records if shard_index(r) == 1)
    app.run_all([r for r in records if r is not bad])
    for attempt in (1, 2, 3):
        app.deliver(bad, attempt=attempt)
    view = app.get(job_id)
    assert view["status"] == "PARTIAL"
    stored = app.shard_items(job_id)
    assert len(stored) == SMALL_JOB["shards"] - 1
    worker_gb_s = sum(int(s["duration_ms"]) / 1000 * int(s["memory_mb"]) / 1024 for s in stored)
    assert abs(view["cost_estimate"]["lambda_gb_seconds"] - (worker_gb_s + 0.125)) < 1e-12


def test_no_cost_estimate_before_any_shard(app):
    job_id = app.submit()
    assert app.get(job_id)["cost_estimate"] is None
