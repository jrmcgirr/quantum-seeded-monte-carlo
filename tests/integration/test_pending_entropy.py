"""QMC-128 opt-in PENDING_ENTROPY (contract change 008), in process against moto.

Seams (WS2, confirmed): the seeder event is ``{"qmc_seed": job_id, "attempt": n}`` routed by
``qmc_api.handler.handle``; the self-invoke is ``lambda.invoke(InvocationType="Event")``; the
backoff sleep is ``qmc_api.pending._sleep``. moto has no async Lambda, so ``Invoke`` calls are
captured here and fed back to the handler by ``seed()``. The 2 s Qrypt gap would hold the seeder
back in a fast test, so ``gap_elapsed()`` deletes ``RL#qrypt/LAST`` (what 2 s of real time does).
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from botocore.client import BaseClient

from .conftest import SMALL_JOB, Context

QUEUE = {**SMALL_JOB, "on_entropy_failure": "queue"}
RETRYABLE_QRYPT = [
    "QRYPT_RATE_LIMITED",
    "QRYPT_SERVER_ERROR",
    "QRYPT_ENTROPY_UNAVAILABLE",
    "QRYPT_TIMEOUT",
    "QRYPT_UNREACHABLE",
]
FAIL_FAST_QRYPT = ["QRYPT_AUTH_FAILED", "QRYPT_FORBIDDEN", "QRYPT_BAD_REQUEST"]
# Seconds slept before attempts 1–4: PENDING_BACKOFF_MS "2000,4000,8000" (last value repeated),
# each clipped to remaining − QRYPT_DEADLINE_MS − 500 − 1000 ms (WS2). With the 10 s API timeout
# (the harness Context reports 10,000 ms, like Lambda) the 8 s step is clipped to 6 s.
BACKOFF_S = [2.0, 4.0, 6.0, 6.0]


@pytest.fixture
def pending(app, monkeypatch):
    """The harness plus captured self-invokes, a recorded backoff and helpers."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "qmc-test-api")
    invokes: list[dict] = []
    sleeps: list[float] = []
    inner = BaseClient._make_api_call

    def capture(self, op, params):
        if self.meta.service_model.service_name == "lambda" and op == "Invoke":
            assert params["InvocationType"] == "Event"
            if app.invoke_error:
                raise app.invoke_error
            invokes.append(json.loads(params["Payload"]))
            return {"StatusCode": 202}
        return inner(self, op, params)

    monkeypatch.setattr(BaseClient, "_make_api_call", capture)
    app.invoke_error = None
    monkeypatch.setattr(sys.modules["qmc_api.pending"], "_sleep", sleeps.append)
    app.invokes, app.sleeps = invokes, sleeps
    return app


def qrypt():
    return importlib.import_module("qmc_api.qrypt")


def failing_source(*failures):
    from qmc_core.errors import ErrorCode

    src = qrypt().FakeEntropySource(
        seed=b"qmc-pending-v1",
        failures=[ErrorCode[f] if isinstance(f, str) else f for f in failures],
    )
    qrypt().set_entropy_source(src)
    return src


def gap_elapsed(app):
    app.table.delete_item(Key={"pk": "RL#qrypt", "sk": "LAST"})


def seed(app, event):
    with app.iam_guard("api"):
        assert app.api.handle(event, Context()) is None  # the seeder never returns HTTP


def items_for(app, job_id):
    return [i for i in app.table.scan()["Items"] if job_id in json.dumps(i, default=str)]


def assert_pending_unseeded(app, job_id):
    item = app.job_item(job_id)
    assert item["status"] == "PENDING_ENTROPY"
    assert {"master_seed_hex", "seed_provenance", "deadline_at"}.isdisjoint(item), item
    # No SQS message for this job before a Qrypt-derived seed (other jobs' messages may exist).
    assert all(json.loads(r["body"])["job_id"] != job_id for r in app.receive_all())


@pytest.mark.parametrize("code", RETRYABLE_QRYPT)
def test_retryable_qrypt_failure_queues_then_seeds_and_completes(pending, code):
    failing_source(code)
    r = pending.call("POST", "/jobs", QUEUE)
    assert (r.status, r.body["status"]) == (202, "PENDING_ENTROPY"), r.body
    job_id = r.body["job_id"]
    assert_pending_unseeded(pending, job_id)
    assert pending.invokes == [{"qmc_seed": job_id, "attempt": 1}]
    assert pending.call("POST", f"/jobs/{job_id}/replay").status == 409  # not replayable yet

    gap_elapsed(pending)
    seed(pending, pending.invokes[-1])
    item = pending.job_item(job_id)
    assert item["status"] == "QUEUED" and len(item["master_seed_hex"]) == 64
    assert item["seed_provenance"]["source"] == "fake"  # the fake Qrypt; never local/time
    pending.run_all(pending.receive_all())
    assert pending.get(job_id)["status"] == "COMPLETE"


def test_our_limiter_queues_too(pending):
    """ENTROPY_RATE_LIMITED is retryable: a second submit inside the gap queues instead of 429."""
    pending.submit()  # takes the gap slot
    r = pending.call("POST", "/jobs", QUEUE)
    assert (r.status, r.body["status"]) == (202, "PENDING_ENTROPY"), r.body
    assert_pending_unseeded(pending, r.body["job_id"])


@pytest.mark.parametrize("code", FAIL_FAST_QRYPT)
def test_fail_fast_codes_never_queue_and_write_nothing(pending, code):
    failing_source(code)
    r = pending.call("POST", "/jobs", QUEUE)
    assert r.status >= 400 and r.body["error"]["code"] == code
    assert [i for i in pending.table.scan()["Items"] if not i["pk"].startswith("RL#")] == []
    assert pending.invokes == [] and pending.receive_all() == []


def test_bad_response_fails_fast(pending):
    failing_source((200, b"not json"))
    r = pending.call("POST", "/jobs", QUEUE)
    assert r.body["error"]["code"] == "QRYPT_BAD_RESPONSE"
    assert pending.invokes == []


def test_secret_unavailable_fails_fast(pending):
    import boto3

    boto3.client("ssm", region_name="us-east-2").delete_parameter(Name="/qmc/qrypt-api-key")
    r = pending.call("POST", "/jobs", QUEUE)
    assert r.body["error"]["code"] == "SECRET_UNAVAILABLE"
    assert pending.invokes == []
    # §7.5 takes the limiter slot (step 2) before reading SSM (step 3): only RL# may exist.
    assert [i for i in pending.table.scan()["Items"] if not i["pk"].startswith("RL#")] == []


def test_default_reject_is_unchanged(pending):
    failing_source("QRYPT_SERVER_ERROR")
    r = pending.call("POST", "/jobs", SMALL_JOB)  # no opt-in
    assert r.status == 503 and r.body["error"]["code"] == "QRYPT_SERVER_ERROR"
    assert pending.invokes == []
    assert [i for i in pending.table.scan()["Items"] if not i["pk"].startswith("RL#")] == []


def test_exhaustion_ends_failed_naming_the_cause(pending, capsys):
    failing_source(*["QRYPT_SERVER_ERROR"] * 10)
    job_id = pending.call("POST", "/jobs", QUEUE).body["job_id"]
    for _ in range(10):  # follow the self-invokes until they stop
        before = len(pending.invokes)
        gap_elapsed(pending)
        seed(pending, pending.invokes[-1])
        if len(pending.invokes) == before:
            break
    item = pending.job_item(job_id)
    assert item["status"] == "FAILED"
    assert item["fail_reason"] == item["last_entropy_error"] == "QRYPT_SERVER_ERROR"
    emf = [ln for ln in capsys.readouterr().out.splitlines() if '"SeedingFailures":1' in ln]
    assert len(emf) == 1, emf  # once, at the end, not per attempt
    assert [e["attempt"] for e in pending.invokes] == [1, 2, 3, 4]  # PENDING_MAX_ATTEMPTS 4
    assert pending.sleeps == BACKOFF_S, pending.sleeps  # PENDING_BACKOFF_MS "2000,4000,8000"
    assert "master_seed_hex" not in item and pending.receive_all() == []
    assert pending.get(job_id)["status"] == "FAILED"


def test_lost_async_event_expires_on_get(pending):
    failing_source("QRYPT_SERVER_ERROR")
    job_id = pending.call("POST", "/jobs", QUEUE).body["job_id"]
    pending.table.update_item(
        Key={"pk": f"JOB#{job_id}", "sk": "META"},
        UpdateExpression="SET pending_until = :t",
        ExpressionAttributeValues={":t": 1},
    )  # the seeder event never arrives; its deadline has passed
    view = pending.get(job_id)
    assert view["status"] == "FAILED", view
    assert "master_seed_hex" not in pending.job_item(job_id)


def test_pending_cap(pending):
    """MAX_PENDING_ENTROPY (10): the 11th opt-in submit gets the original error, nothing written."""
    failing_source("QRYPT_SERVER_ERROR")  # 1st takes the gap slot and fails; 2nd–11th hit our gap
    rs = [pending.call("POST", "/jobs", QUEUE) for _ in range(11)]
    assert [r.body.get("status") for r in rs[:10]] == ["PENDING_ENTROPY"] * 10
    assert rs[10].status == 429 and rs[10].body["error"]["code"] == "ENTROPY_RATE_LIMITED"
    assert len(pending.invokes) == 10


def test_seeded_pending_replays_bit_identically(pending):
    failing_source("QRYPT_SERVER_ERROR")
    job_id = pending.call("POST", "/jobs", QUEUE).body["job_id"]
    gap_elapsed(pending)
    seed(pending, pending.invokes[-1])
    pending.run_all(pending.receive_all())
    r = pending.call("POST", f"/jobs/{job_id}/replay")
    assert r.status == 202
    pending.run_all(pending.receive_all())
    assert pending.get(r.body["job_id"])["replay_check"]["bit_identical"] is True


def test_backoff_unclipped_with_enough_time_left():
    from qmc_api.pending import backoff_s

    class Plenty:
        def get_remaining_time_in_millis(self):
            return 12_000

    assert [backoff_s(n, Plenty()) for n in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 8.0]


def test_duplicate_or_late_seeder_event_writes_nothing(pending):
    failing_source("QRYPT_SERVER_ERROR")
    job_id = pending.call("POST", "/jobs", QUEUE).body["job_id"]
    event = pending.invokes[-1]
    gap_elapsed(pending)
    seed(pending, event)
    before, n_invokes = pending.job_item(job_id), len(pending.invokes)
    gap_elapsed(pending)
    seed(pending, event)  # redelivered after the job left PENDING_ENTROPY
    assert pending.job_item(job_id) == before and len(pending.invokes) == n_invokes


def test_seeder_past_pending_until_fails_the_job(pending):
    failing_source("QRYPT_SERVER_ERROR")
    job_id = pending.call("POST", "/jobs", QUEUE).body["job_id"]
    pending.table.update_item(
        Key={"pk": f"JOB#{job_id}", "sk": "META"},
        UpdateExpression="SET pending_until = :t",
        ExpressionAttributeValues={":t": 1},
    )
    gap_elapsed(pending)
    seed(pending, pending.invokes[-1])
    item = pending.job_item(job_id)
    assert item["status"] == "FAILED" and "master_seed_hex" not in item


def test_failed_self_invoke_fails_the_job_and_returns_the_original_error(pending):
    from botocore.exceptions import ClientError

    pending.invoke_error = ClientError(
        {"Error": {"Code": "ServiceException", "Message": "x"}}, "Invoke"
    )
    failing_source("QRYPT_SERVER_ERROR")
    r = pending.call("POST", "/jobs", QUEUE)
    assert r.status == 503 and r.body["error"]["code"] == "QRYPT_SERVER_ERROR"
    pending_items = [i for i in pending.table.scan()["Items"] if i["sk"] == "META"]
    assert [i["status"] for i in pending_items] == ["FAILED"]  # never left PENDING_ENTROPY
    assert pending.receive_all() == []


class Short(Context):
    """4.5 s left: room for a 0.5 s wait only (− QRYPT_DEADLINE_MS 2.5 s − 0.5 s − 1 s)."""

    def get_remaining_time_in_millis(self):
        return 4_500


def seed_with(app, event, context):
    with app.iam_guard("api"):
        assert app.api.handle(event, context) is None


def queued_behind_the_gap(app):
    """A PENDING job whose seeder will find our 2 s slot taken (the submit's own call took it)."""
    failing_source("QRYPT_SERVER_ERROR")
    job_id = app.call("POST", "/jobs", QUEUE).body["job_id"]
    return job_id, app.job_item(job_id).get("entropy_attempts")


def test_refused_slot_waits_in_process_without_spending_an_attempt(pending, monkeypatch):
    """James's fix after the live burst: our own gap is waited out (retry_after + 0–2 s), not an
    attempt. From the second sleep on (the first is the backoff) the fake sleep advances the clock
    by what it slept, as a real one would; it may not write (the seeder runs under the IAM guard)."""
    import time

    job_id, before = queued_behind_the_gap(pending)
    real, skew = time.time, [0.0]
    monkeypatch.setattr(time, "time", lambda: real() + skew[0])

    def sleep(s):
        pending.sleeps.append(s)
        if len(pending.sleeps) >= 2:
            skew[0] += s

    monkeypatch.setattr(sys.modules["qmc_api.pending"], "_sleep", sleep)
    seed(pending, pending.invokes[-1])
    item = pending.job_item(job_id)
    assert item["status"] == "QUEUED" and len(item["master_seed_hex"]) == 64
    assert item.get("entropy_attempts") == before  # the refusal spent nothing
    assert pending.invokes == [{"qmc_seed": job_id, "attempt": 1}]  # waited in process
    assert pending.sleeps[0] == 2.0 and 1.0 <= pending.sleeps[1] <= 4.0, pending.sleeps


def test_refused_slot_that_does_not_fit_hops_with_the_same_attempt(pending):
    job_id, before = queued_behind_the_gap(pending)
    seed_with(pending, pending.invokes[-1], Short())
    item = pending.job_item(job_id)
    assert item["status"] == "PENDING_ENTROPY" and item.get("entropy_attempts") == before
    assert_pending_unseeded(pending, job_id)
    hop = pending.invokes[-1]
    assert {k: hop[k] for k in ("qmc_seed", "attempt", "hop")} == {
        "qmc_seed": job_id,
        "attempt": 1,
        "hop": 2,
    }
    assert int(hop["not_before_ms"]) > 0


def test_hop_limit_fails_closed_naming_the_cause(pending):
    """At hop 15 (Lambda drops a ~16-deep self-invoke chain) the job fails, never re-invokes."""
    job_id, _ = queued_behind_the_gap(pending)
    n = len(pending.invokes)
    seed_with(pending, {"qmc_seed": job_id, "attempt": 1, "hop": 15}, Short())
    item = pending.job_item(job_id)
    assert item["status"] == "FAILED" and item["fail_reason"] == "ENTROPY_RATE_LIMITED"
    assert "master_seed_hex" not in item and len(pending.invokes) == n
    assert pending.receive_all() == []
