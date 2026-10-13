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
# Seconds slept before attempts 2, 3, 4 (PENDING_BACKOFF_MS "2000,4000,8000"); pinned with WS2.
BACKOFF_S = [2.0, 4.0, 8.0]


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
            invokes.append(json.loads(params["Payload"]))
            return {"StatusCode": 202}
        return inner(self, op, params)

    monkeypatch.setattr(BaseClient, "_make_api_call", capture)
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
    assert app.receive_all() == []  # no SQS message before a Qrypt-derived seed


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
    assert pending.invokes == [] and pending.table.scan()["Items"] == []


def test_default_reject_is_unchanged(pending):
    failing_source("QRYPT_SERVER_ERROR")
    r = pending.call("POST", "/jobs", SMALL_JOB)  # no opt-in
    assert r.status == 503 and r.body["error"]["code"] == "QRYPT_SERVER_ERROR"
    assert pending.invokes == []
    assert [i for i in pending.table.scan()["Items"] if not i["pk"].startswith("RL#")] == []


def test_exhaustion_ends_failed_naming_the_cause(pending):
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
    assert item["fail_reason"] and "QRYPT_SERVER_ERROR" in str(item.get("last_entropy_error", ""))
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
