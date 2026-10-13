"""QMC-122: POST /jobs. Fail closed, limiter, entropy uniqueness, enqueue failure."""

from __future__ import annotations

import json

import pytest

from qmc_core.errors import ErrorCode
from tests.api.conftest import REF

QRYPT_FAILURES = [
    (ErrorCode.QRYPT_BAD_REQUEST, 500),
    (ErrorCode.QRYPT_AUTH_FAILED, 503),
    (ErrorCode.QRYPT_QUOTA_EXHAUSTED, 503),
    (ErrorCode.QRYPT_RATE_LIMITED, 503),
    (ErrorCode.QRYPT_SERVER_ERROR, 503),
    (ErrorCode.QRYPT_ENTROPY_UNAVAILABLE, 503),
    (ErrorCode.QRYPT_TIMEOUT, 503),
    (ErrorCode.QRYPT_UNREACHABLE, 503),
    (ErrorCode.QRYPT_BAD_RESPONSE, 502),
]


def emf_lines(out: str, name: str) -> list[dict]:
    return [o for o in map(json.loads, out.splitlines()) if name in o and "_aws" in o]


def test_happy_path_writes_job_joblist_and_one_message_per_shard(api, capsys):
    status, _, body = api.call("POST", "/jobs", REF)
    assert status == 202, body
    job_id = body["job_id"]
    assert body["status"] == "QUEUED" and len(body["master_seed_hex"]) == 64
    assert body["links"] == {"self": f"/jobs/{job_id}", "replay": f"/jobs/{job_id}/replay"}

    [job] = api.items("JOB#")
    assert job["status"] == "QUEUED" and job["master_seed_hex"] == body["master_seed_hex"]
    assert job["params"] == {"S0": "100.0", "K": "100.0", "r": "0.05", "sigma": "0.2", "T": "1.0"}
    assert [int(n) for n in job["paths_per_shard"]] == [250, 250, 250, 250]
    assert job["seed_provenance"]["source"] == "fake"
    assert "deadline_at" in job and "fault_injection" not in job
    assert len(api.items("JOBLIST")) == 1 and len(api.items("ENTROPY#")) == 1

    msgs = api.messages()
    assert sorted(m["shard_index"] for m in msgs) == [0, 1, 2, 3]
    assert {m["master_seed_hex"] for m in msgs} == {body["master_seed_hex"]}
    assert not any(m["fail"] for m in msgs)

    out = capsys.readouterr().out
    for name in ("JobsSubmitted", "EntropyKiB", "QryptLatencyMs"):
        assert len(emf_lines(out, name)) == 1
    assert api.fake.calls == 1


def test_eleven_shards_send_two_batches(api):
    assert api.call("POST", "/jobs", {**REF, "shards": 11})[0] == 202
    assert len(api.messages()) == 11


@pytest.mark.parametrize(("code", "http"), QRYPT_FAILURES)
def test_qrypt_failure_writes_nothing_and_names_cause(api, capsys, code, http):
    api.fake.failures = [code, code]
    status, _, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (http, code.name)
    assert [i["pk"] for i in api.items()] == ["RL#qrypt"]  # only limiter slots
    assert api.queued() == 0
    [failure] = emf_lines(capsys.readouterr().out, "SeedingFailures")
    assert failure["cause"] == code.name


def test_401_calls_qrypt_once(api):
    api.fake.failures = [ErrorCode.QRYPT_AUTH_FAILED]
    api.call("POST", "/jobs", REF)
    assert api.fake.calls == 1


def test_secret_failure_is_503_and_writes_nothing(api, monkeypatch):
    monkeypatch.setenv("QRYPT_API_KEY_PARAM", "/qmc/missing")
    status, _, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (503, "SECRET_UNAVAILABLE")
    assert [i["pk"] for i in api.items()] == ["RL#qrypt"] and api.queued() == 0
    assert api.fake.calls == 0


def test_qrypt_rate_limited_sets_retry_after_10(api):
    api.fake.failures = [ErrorCode.QRYPT_RATE_LIMITED] * 2
    _, headers, _ = api.call("POST", "/jobs", REF)
    assert headers["retry-after"] == "10"


def test_retry_takes_a_second_limiter_slot(api):
    api.fake.failures = [ErrorCode.QRYPT_SERVER_ERROR]
    assert api.call("POST", "/jobs", REF)[0] == 202
    [window] = api.items("RL#")
    assert int(window["n"]) == 2 and api.fake.calls == 2


def test_sixteenth_submit_in_a_window_is_429(api, monkeypatch):
    from qmc_api import submit

    monkeypatch.setattr(submit.time, "time", lambda: 1_800_000_003.0)  # 7 s left in the window
    statuses = [api.call("POST", "/jobs", {**REF, "shards": 1})[0] for _ in range(15)]
    assert statuses == [202] * 15
    status, headers, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (429, "ENTROPY_RATE_LIMITED")
    assert headers["retry-after"] == "7"
    assert api.fake.calls == 15


def test_repeated_entropy_block_is_502_and_no_job(api):
    from qmc_api import qrypt

    assert api.call("POST", "/jobs", REF)[0] == 202
    qrypt.set_entropy_source(qrypt.FakeEntropySource())  # same seed: the same first block
    status, _, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (502, "QRYPT_BAD_RESPONSE")
    assert len(api.items("JOB#")) == 1


def test_fault_injection_rejected_unless_enabled(api, monkeypatch):
    body = {**REF, "fault_injection": {"fail_shards": [1]}}
    assert api.call("POST", "/jobs", body)[2]["error"]["code"] == "FAULT_INJECTION_DISABLED"
    assert api.fake.calls == 0
    monkeypatch.setenv("FAULT_INJECTION_ENABLED", "true")
    assert api.call("POST", "/jobs", body)[0] == 202
    assert {m["shard_index"]: m["fail"] for m in api.messages()} == {
        0: False,
        1: True,
        2: False,
        3: False,
    }


def test_enqueue_failure_marks_job_failed(api, monkeypatch):
    from qmc_api import aws

    real = aws.sqs()

    class Flaky:
        def send_message_batch(self, QueueUrl, Entries):
            resp = real.send_message_batch(QueueUrl=QueueUrl, Entries=Entries[1:])
            return {**resp, "Failed": [{"Id": Entries[0]["Id"], "SenderFault": False}]}

    monkeypatch.setattr(aws, "sqs", lambda: Flaky())
    status, _, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (503, "ENQUEUE_FAILED")
    [job] = api.items("JOB#")
    assert job["status"] == "FAILED"


def test_throttled_joblist_put_leaves_job_failed_not_queued(api, monkeypatch):
    from botocore.exceptions import ClientError

    from qmc_core import ddb

    def boom(item):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    monkeypatch.setattr(ddb, "joblist_put", boom)
    status, _, body = api.call("POST", "/jobs", REF)
    assert (status, body["error"]["code"]) == (503, "ENQUEUE_FAILED")
    [job] = api.items("JOB#")
    assert job["status"] == "FAILED" and api.queued() == 0
