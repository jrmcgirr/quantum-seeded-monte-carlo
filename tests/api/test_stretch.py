"""Stretch: QMC-128 PENDING_ENTROPY (contract change 008), QMC-127 Step Functions watcher (007),
QMC-126 cost estimate in the view."""

from __future__ import annotations

import json

import pytest

from qmc_core import ddb
from qmc_core.errors import ErrorCode
from tests.api.conftest import REF, Ctx
from tests.api.test_status_replay import land

QUEUE = {**REF, "on_entropy_failure": "queue"}
UNAVAILABLE = ErrorCode.QRYPT_ENTROPY_UNAVAILABLE


@pytest.fixture
def invokes(api, monkeypatch):
    """Capture lambda.Invoke inside botocore (the IAM guard still records it); no request is sent."""
    import boto3
    from botocore.awsrequest import AWSResponse

    from qmc_api import aws

    client = boto3.client("lambda", region_name="us-east-2")
    seen: list[dict] = []

    def hook(params, **kw):
        seen.append(
            {
                "path": params["url_path"],
                "type": params["headers"].get("X-Amz-Invocation-Type"),
                "event": json.loads(params["body"]),
            }
        )
        return AWSResponse(None, 202, {}, None), {"StatusCode": 202}

    client.meta.events.register("before-call.lambda.Invoke", hook)
    monkeypatch.setattr(aws, "lambda_", lambda: client)
    return seen


@pytest.fixture
def sleeps(monkeypatch):
    from qmc_api import pending

    slept: list[float] = []
    monkeypatch.setattr(pending, "_sleep", slept.append)
    return slept


@pytest.fixture
def seeder(api, iam_guard):
    from qmc_api import handler

    def run(job_id: str, attempt: int, remaining_ms: int = 20_000):
        with iam_guard("api"):
            assert (
                handler.handle({"qmc_seed": job_id, "attempt": attempt}, Ctx(remaining_ms)) is None
            )

    return run


def job_item(api, job_id: str) -> dict:
    return api.table.get_item(Key=ddb.job_key(job_id), ConsistentRead=True)["Item"]


def queue_one(api, invokes) -> str:
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]  # the submit's call and its one retry
    status, _, body = api.call("POST", "/jobs", QUEUE)
    assert status == 202, body
    assert body["status"] == "PENDING_ENTROPY" and "master_seed_hex" not in body
    return body["job_id"]


# ---- QMC-128 PENDING_ENTROPY -----------------------------------------------------------------


def test_queue_writes_an_unseeded_pending_job_and_invokes_the_seeder(api, invokes, capsys):
    job_id = queue_one(api, invokes)
    job = job_item(api, job_id)
    assert job["status"] == "PENDING_ENTROPY"
    assert job["last_entropy_error"] == UNAVAILABLE.name
    assert (
        "master_seed_hex" not in job and "seed_provenance" not in job and "deadline_at" not in job
    )
    assert len(api.items("JOBLIST")) == 1 and api.queued() == 0
    assert invokes == [
        {
            "path": "/2015-03-31/functions/qmc-dev-api/invocations",
            "type": "Event",
            "event": {"qmc_seed": job_id, "attempt": 1},
        }
    ]
    assert '"SeedingFailures"' not in capsys.readouterr().out  # not failed (yet)


def test_seeder_seeds_from_qrypt_and_enqueues(api, invokes, sleeps, seeder, capsys):
    job_id = queue_one(api, invokes)
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "QUEUED" and len(job["master_seed_hex"]) == 64
    assert job["seed_provenance"]["source"] == "fake" and "deadline_at" in job
    assert sorted(m["shard_index"] for m in api.messages()) == [0, 1, 2, 3]
    assert sleeps == [2.0]
    assert len(invokes) == 1  # no further attempt
    out = capsys.readouterr().out
    assert '"JobsSubmitted"' in out and '"SeedingFailures"' not in out


def test_default_reject_is_unchanged(api, invokes):
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    status, _, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (503, UNAVAILABLE.name)
    assert {i["pk"] for i in api.items()} == {"RL#qrypt"} and not invokes


@pytest.mark.parametrize(
    "code", [ErrorCode.QRYPT_AUTH_FAILED, ErrorCode.QRYPT_FORBIDDEN, ErrorCode.QRYPT_BAD_RESPONSE]
)
def test_non_retryable_failure_fails_fast_even_when_queued(api, invokes, code):
    api.fake.failures = [code]
    status, _, body = api.call("POST", "/jobs", QUEUE)
    assert (status, body["error"]["code"]) == (code.http_status, code.name)
    assert {i["pk"] for i in api.items()} == {"RL#qrypt"} and not invokes


def test_pending_cap_returns_the_original_error(api, invokes, monkeypatch):
    monkeypatch.setenv("MAX_PENDING_ENTROPY", "1")
    queue_one(api, invokes)
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    status, _, body = api.call("POST", "/jobs", QUEUE)
    assert (status, body["error"]["code"]) == (503, UNAVAILABLE.name)
    assert len(api.items("JOB#")) == 1 and len(invokes) == 1


def test_retryable_failures_back_off_then_fail_after_max_attempts(
    api, invokes, sleeps, seeder, capsys
):
    job_id = queue_one(api, invokes)
    capsys.readouterr()
    for attempt in (1, 2, 3, 4):
        api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
        seeder(job_id, attempt)
    assert [i["event"]["attempt"] for i in invokes] == [1, 2, 3, 4]
    assert sleeps == [2.0, 4.0, 8.0, 8.0]
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == UNAVAILABLE.name
    assert job["last_entropy_error"] == UNAVAILABLE.name and "master_seed_hex" not in job
    assert api.queued() == 0
    out = capsys.readouterr().out
    assert out.count('"SeedingFailures":1') == 1  # one EMF value at exhaustion


def test_non_retryable_failure_on_attempt_2_fails_at_once(api, invokes, sleeps, seeder):
    job_id = queue_one(api, invokes)
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    seeder(job_id, 1)
    api.fake.failures = [ErrorCode.QRYPT_FORBIDDEN]
    seeder(job_id, 2)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == "QRYPT_FORBIDDEN"
    assert [i["event"]["attempt"] for i in invokes] == [1, 2]  # no attempt 3


def test_lost_event_get_past_pending_until_fails_the_job(api, invokes):
    job_id = queue_one(api, invokes)
    api.table.update_item(
        Key=ddb.job_key(job_id),
        UpdateExpression="SET pending_until = :one",
        ExpressionAttributeValues={":one": 1},
    )
    status, _, view = api.call("GET", f"/jobs/{job_id}")
    assert status == 200 and view["status"] == "FAILED"
    assert view["fail_reason"] == UNAVAILABLE.name and view["master_seed_hex"] is None


def test_seeder_racing_get_expiry_never_queues_a_failed_job(
    api, invokes, sleeps, seeder, monkeypatch
):
    """GET's pending_fail lands between the seeder's Qrypt fetch and its pending_to_queued."""
    from qmc_api import pending, submit

    job_id = queue_one(api, invokes)
    real = submit.seed_job

    def seed_then_expire(table, jid, context):
        result = real(table, jid, context)
        pending.fail_pending(table, jid, "PENDING_EXPIRED")
        return result

    monkeypatch.setattr(submit, "seed_job", seed_then_expire)
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == "PENDING_EXPIRED"
    assert "master_seed_hex" not in job and api.queued() == 0


def test_seeder_ignores_a_job_that_is_no_longer_pending(api, invokes, sleeps, seeder):
    job_id = queue_one(api, invokes)
    seeder(job_id, 1)
    calls = api.fake.calls
    api.messages()
    seeder(job_id, 1)  # a duplicate event
    assert api.fake.calls == calls and api.queued() == 0 and not sleeps[1:]


def test_seeder_with_a_bad_event_never_raises(api, seeder):
    seeder("not-a-job", 1)
    seeder("0" * 32, 1)  # no such job


def test_failed_invoke_at_submit_fails_the_pending_job(api, monkeypatch):
    from qmc_api import aws

    class Broken:
        def invoke(self, **kw):
            raise RuntimeError("no invoke")

    monkeypatch.setattr(aws, "lambda_", lambda: Broken())
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    status, _, body = api.call("POST", "/jobs", QUEUE)
    assert (status, body["error"]["code"]) == (503, UNAVAILABLE.name)
    [job] = [j for j in api.items("JOB#") if j["sk"] == "META"]
    assert job["status"] == "FAILED"


def test_pending_view_and_replay_409(api, invokes):
    job_id = queue_one(api, invokes)
    status, _, view = api.call("GET", f"/jobs/{job_id}")
    assert status == 200 and view["status"] == "PENDING_ENTROPY"
    assert view["master_seed_hex"] is None and view["last_entropy_error"] == UNAVAILABLE.name
    assert view["progress"] == {"done": 0, "failed": 0, "total": 4}
    status, _, body = api.call("POST", f"/jobs/{job_id}/replay")
    assert (status, body["error"]["code"]) == (409, "JOB_NOT_REPLAYABLE")


def test_backoff_is_bounded_by_remaining_time():
    from qmc_api import pending

    assert pending.backoff_s(3, Ctx(20_000)) == 8.0
    assert pending.backoff_s(3, Ctx(10_000)) == 6.0  # 10 s - 2.5 s Qrypt - 0.5 s - 1 s
    assert pending.backoff_s(9, Ctx(20_000)) == 8.0  # the last value repeats
    assert pending.backoff_s(1, Ctx(3_000)) == 0.0


# ---- QMC-127 Step Functions watcher ---------------------------------------------------------


@pytest.fixture
def sfn_arn(api, monkeypatch):
    import boto3

    sfn = boto3.client("stepfunctions", region_name="us-east-2")
    arn = sfn.create_state_machine(
        name="qmc-test-watcher",
        definition=json.dumps({"StartAt": "S", "States": {"S": {"Type": "Succeed"}}}),
        roleArn="arn:aws:iam::123456789012:role/qmc-test-watcher",
    )["stateMachineArn"]
    monkeypatch.setenv("SFN_ARN", arn)
    return arn


def executions(arn: str) -> list[dict]:
    import boto3

    sfn = boto3.client("stepfunctions", region_name="us-east-2")
    out = []
    for e in sfn.list_executions(stateMachineArn=arn)["executions"]:
        desc = sfn.describe_execution(executionArn=e["executionArn"])
        out.append({"name": e["name"], "input": json.loads(desc["input"])})
    return out


def test_submit_and_replay_start_a_watcher_named_after_the_job(api, sfn_arn):
    job_id = api.call("POST", "/jobs", REF)[2]["job_id"]
    for i in range(4):
        land(api.table, job_id, i)
    api.call("GET", f"/jobs/{job_id}")
    replay_id = api.call("POST", f"/jobs/{job_id}/replay")[2]["job_id"]
    assert sorted(executions(sfn_arn), key=lambda e: e["name"]) == sorted(
        [
            {"name": job_id, "input": {"job_id": job_id}},
            {"name": replay_id, "input": {"job_id": replay_id}},
        ],
        key=lambda e: e["name"],
    )


def test_seeded_pending_job_starts_a_watcher(api, invokes, sleeps, seeder, sfn_arn):
    job_id = queue_one(api, invokes)
    assert executions(sfn_arn) == []  # not while PENDING
    seeder(job_id, 1)
    assert [e["name"] for e in executions(sfn_arn)] == [job_id]


def test_watcher_off_makes_no_call(api, iam_guard):
    from qmc_api import handler
    from tests.api.conftest import event

    with iam_guard("api") as calls:
        handler.handle(event("POST", "/jobs", REF), Ctx())
    assert not [c for c in calls if c[0] == "states"]


def test_watcher_already_exists_is_success(api, sfn_arn, capsys):
    from qmc_api import submit

    submit.start_watch("a" * 32)
    submit.start_watch("a" * 32)
    assert "sfn_start_failed" not in capsys.readouterr().out
    assert len(executions(sfn_arn)) == 1


def test_watcher_failure_is_logged_and_never_changes_the_response(api, monkeypatch, capsys):
    monkeypatch.setenv("SFN_ARN", "arn:aws:states:us-east-2:123456789012:stateMachine:missing")
    status, _, _ = api.call("POST", "/jobs", REF)
    assert status == 202
    assert "sfn_start_failed" in capsys.readouterr().out


# ---- QMC-126 cost estimate --------------------------------------------------------------------


def test_complete_job_view_has_a_cost_estimate(api):
    job_id = api.call("POST", "/jobs", REF)[2]["job_id"]
    assert api.call("GET", f"/jobs/{job_id}")[2]["cost_estimate"] is None  # not terminal
    for i in range(4):
        land(api.table, job_id, i)
    cost = api.call("GET", f"/jobs/{job_id}")[2]["cost_estimate"]
    assert cost["usd_after_free_tier"] == 0.0 and cost["lambda_gb_seconds"] > 0


# ---- QMC-128 defensive paths ------------------------------------------------------------------


def expire_pending(api, job_id: str) -> None:
    api.table.update_item(
        Key=ddb.job_key(job_id),
        UpdateExpression="SET pending_until = :one",
        ExpressionAttributeValues={":one": 1},
    )


def test_seeder_past_pending_until_fails_the_job_itself(api, invokes, sleeps, seeder):
    job_id = queue_one(api, invokes)
    expire_pending(api, job_id)
    calls = api.fake.calls
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == UNAVAILABLE.name
    assert api.fake.calls == calls and not sleeps  # no Qrypt call, no backoff


def test_seeder_unexpected_error_fails_the_job_as_internal_error(
    api, invokes, sleeps, seeder, monkeypatch
):
    from qmc_api import submit

    job_id = queue_one(api, invokes)

    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(submit, "seed_job", boom)
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == "INTERNAL_ERROR"


def test_seeder_reinvoke_failure_fails_the_job(api, invokes, sleeps, seeder, monkeypatch):
    from qmc_api import pending

    job_id = queue_one(api, invokes)

    def broken(job_id, attempt):
        raise RuntimeError("no invoke")

    monkeypatch.setattr(pending, "invoke_seeder", broken)
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == UNAVAILABLE.name


def test_attempt_failed_refused_when_the_job_moved_on(api, invokes, sleeps, seeder, monkeypatch):
    """GET's expiry lands between the seeder's failed Qrypt call and its attempt bookkeeping."""
    from qmc_api import pending, submit

    job_id = queue_one(api, invokes)
    real = submit.seed_job

    def fail_after_expiry(table, jid, context):
        pending.fail_pending(table, jid, "PENDING_EXPIRED")
        return real(table, jid, context)

    monkeypatch.setattr(submit, "seed_job", fail_after_expiry)
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == "PENDING_EXPIRED"
    assert int(job["entropy_attempts"]) == 1 and len(invokes) == 1  # no bookkeeping, no attempt 2


def test_second_pending_fail_is_refused_quietly(api, invokes, capsys):
    from qmc_api import aws, pending

    job_id = queue_one(api, invokes)
    pending.fail_pending(aws.table(), job_id, "PENDING_EXPIRED")
    capsys.readouterr()
    pending.fail_pending(aws.table(), job_id, "QRYPT_TIMEOUT")
    out = capsys.readouterr().out
    assert "pending_fail_refused" in out and '"SeedingFailures":1' not in out
    assert job_item(api, job_id)["fail_reason"] == "PENDING_EXPIRED"


# ---- WS3 review of !43 ------------------------------------------------------------------------


def test_duplicate_seeder_delivery_does_not_double_the_retry_chain(
    api, invokes, sleeps, seeder, capsys
):
    job_id = queue_one(api, invokes)
    for _ in range(2):  # the same async event delivered twice (at-least-once)
        api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
        seeder(job_id, 1)
    assert [i["event"]["attempt"] for i in invokes] == [1, 2]  # one attempt 2, not two
    assert int(job_item(api, job_id)["entropy_attempts"]) == 3
    assert "duplicate_seeder" in capsys.readouterr().out


def test_seeder_publishes_without_reading_the_job_again(api, invokes, sleeps, seeder, monkeypatch):
    """A failed read after pending_to_queued must not leave a QUEUED job without messages."""
    from qmc_api import jobs

    job_id = queue_one(api, invokes)
    real, reads = jobs.read, []

    def read_once(table, jid):
        reads.append(jid)
        if len(reads) > 1:
            raise RuntimeError("throttled")
        return real(table, jid)

    monkeypatch.setattr(jobs, "read", read_once)
    seeder(job_id, 1)
    assert len(reads) == 1
    assert job_item(api, job_id)["status"] == "QUEUED" and len(api.messages()) == 4


def test_seeder_enqueue_failure_ends_failed_not_queued(api, invokes, sleeps, seeder, monkeypatch):
    from qmc_api import aws

    job_id = queue_one(api, invokes)

    class Broken:
        def send_message_batch(self, **kw):
            raise RuntimeError("sqs down")

    monkeypatch.setattr(aws, "sqs", lambda: Broken())
    seeder(job_id, 1)
    job = job_item(api, job_id)
    assert job["status"] == "FAILED" and job["fail_reason"] == "RuntimeError"


def test_joblist_failure_when_queueing_fails_the_pending_job(api, invokes, monkeypatch):
    from botocore.exceptions import ClientError

    from qmc_core import ddb as core_ddb

    def throttled(item):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    monkeypatch.setattr(core_ddb, "joblist_put", throttled)
    api.fake.failures = [UNAVAILABLE, UNAVAILABLE]
    status, _, body = api.call("POST", "/jobs", QUEUE)
    assert (status, body["error"]["code"]) == (503, UNAVAILABLE.name)
    [job] = [j for j in api.items("JOB#") if j["sk"] == "META"]
    assert job["status"] == "FAILED" and not invokes
