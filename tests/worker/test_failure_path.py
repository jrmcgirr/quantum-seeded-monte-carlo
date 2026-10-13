"""QMC-131: visibility on every caught failure, last-attempt marking, PARTIAL, fault injection."""

from __future__ import annotations

import dataclasses

import boto3
import pytest

from qmc_core import messages
from tests.worker.conftest import event, events, job_item, metric_total, record, stdout_lines

FAILED = 3


class Queue:
    """The real (moto) main queue, so ChangeMessageVisibility gets a valid receipt handle."""

    def __init__(self, urls):
        self.sqs = boto3.client("sqs", region_name="us-east-2")
        self.urls = urls

    def send(self, msg):
        self.sqs.send_message(QueueUrl=self.urls["main"], MessageBody=messages.encode(msg))

    def receive(self, url=None):
        out = self.sqs.receive_message(
            QueueUrl=url or self.urls["main"], AttributeNames=["All"], MaxNumberOfMessages=1
        ).get("Messages", [])
        if not out:
            return None
        m = out[0]
        return {
            "messageId": m["MessageId"],
            "receiptHandle": m["ReceiptHandle"],
            "body": m["Body"],
            "attributes": {"ApproximateReceiveCount": m["Attributes"]["ApproximateReceiveCount"]},
            "eventSource": "aws:sqs",
        }

    def expire(self, rec):
        """Stand in for the 5 s wait: make the message visible again now."""
        self.sqs.change_message_visibility(
            QueueUrl=self.urls["main"], ReceiptHandle=rec["receiptHandle"], VisibilityTimeout=0
        )


@pytest.fixture
def queue(queues):
    return Queue(queues)


@pytest.fixture
def visibility_calls(worker, monkeypatch):
    calls = []
    client = worker._sqs()
    real = client.change_message_visibility

    def spy(**kw):
        calls.append(kw["VisibilityTimeout"])
        return real(**kw)

    monkeypatch.setattr(client, "change_message_visibility", spy)
    return calls


@pytest.fixture
def injection_on(monkeypatch):
    monkeypatch.setenv("FAULT_INJECTION_ENABLED", "true")


def complete_others(worker, ctx, msgs, skip):
    for m in msgs:
        if m.shard_index != skip:
            assert worker.handle(event(record(m)), ctx) == {"batchItemFailures": []}


def test_forced_failure_three_attempts_then_partial_and_dlq(
    worker, ctx, make_job, ddb_table, queue, visibility_calls, injection_on, iam_guard, capsys
):
    item, msgs = make_job(fail_shards=[FAILED])
    assert msgs[FAILED].fail
    queue.send(msgs[FAILED])
    capsys.readouterr()

    with iam_guard("worker"):
        complete_others(worker, ctx, msgs, FAILED)
        for attempt in (1, 2, 3):
            rec = queue.receive()
            assert rec["attributes"]["ApproximateReceiveCount"] == str(attempt)
            out = worker.handle(event(rec), ctx)
            assert out == {"batchItemFailures": [{"itemIdentifier": rec["messageId"]}]}
            job = job_item(ddb_table, item["job_id"])
            if attempt < 3:
                assert job["status"] == "QUEUED" and "failed_shards" not in job
            queue.expire(rec)

    assert visibility_calls == [5, 5, 5]
    assert job["status"] == "PARTIAL"
    assert job["failed_shards"] == {FAILED}
    assert "result" not in job and job["partial_result"]["n"] == item["paths"] - int(
        item["paths_per_shard"][FAILED]
    )
    lines = stdout_lines(capsys)
    assert metric_total(lines, "ShardFailures") == 1
    assert metric_total(lines, "JobsPartial") == 1
    assert metric_total(lines, "JobsCompleted") == 0

    # SQS redrives on the receive after the third: the message lands in the DLQ.
    assert queue.receive() is None
    assert queue.receive(queue.urls["dlq"]) is not None


def test_fail_flag_ignored_when_injection_disabled(worker, ctx, make_job, ddb_table, iam_guard):
    item, msgs = make_job(fail_shards=[FAILED])
    with iam_guard("worker"):
        for m in msgs:
            assert worker.handle(event(record(m, attempt=3)), ctx) == {"batchItemFailures": []}
    assert job_item(ddb_table, item["job_id"])["status"] == "COMPLETE"


def test_done_shard_is_never_marked_failed(
    worker, ctx, make_job, ddb_table, queue, injection_on, iam_guard, capsys
):
    """A failure after the shard was counted: mark_failed is refused and the job still completes."""
    item, msgs = make_job()
    queue.send(dataclasses.replace(msgs[FAILED], fail=True))
    capsys.readouterr()

    with iam_guard("worker") as calls:
        worker.handle(event(record(msgs[FAILED])), ctx)  # counted done first
        for _ in range(3):
            rec = queue.receive()
            assert worker.handle(event(rec), ctx)["batchItemFailures"]
            queue.expire(rec)
        complete_others(worker, ctx, msgs, FAILED)

    job = job_item(ddb_table, item["job_id"])
    assert "failed_shards" not in job
    assert job["status"] == "COMPLETE"
    lines = stdout_lines(capsys)
    assert events(lines, "mark_failed_refused")
    assert metric_total(lines, "ShardFailures") == 0
    assert ("dynamodb", "GetItem") not in calls


def test_poison_message_reported_without_aws_calls(worker, ctx, iam_guard, capsys):
    bad = record('{"v": 1, "job_id": "nope"}')
    with iam_guard("worker") as calls:
        out = worker.handle(event(bad), ctx)
    assert out == {"batchItemFailures": [{"itemIdentifier": bad["messageId"]}]}
    assert calls == []  # no job to mark, no visibility change (§7.6 step 1)
    assert events(stdout_lines(capsys), "poison_message")
