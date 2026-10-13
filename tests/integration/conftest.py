"""In-process harness (QMC-141): Function URL events into the API handler, SQS messages from moto
pumped into the worker handler, the way the event source mapping would. No AWS account, no token.

Assumptions about WS2/WS3 code live here only: the handler entry points (HANDOFF §8.2), the
entropy seam (§4.1) and ``FakeEntropySource``'s constructor.
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
import uuid
from dataclasses import dataclass, field

import pytest

from qmc_core.aggregate import aggregate
from qmc_core.codec import to_hex
from qmc_core.params import split_paths
from qmc_core.seeds import master_int_from_hex

REFERENCE = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}
SMALL_JOB = {**REFERENCE, "paths": 20_000, "shards": 4}


class Crash(BaseException):
    """Simulates the Lambda process dying: handlers catch Exception, never this."""


@dataclass
class Context:
    aws_request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    function_name: str = "qmc-test"
    memory_limit_in_mb: int = 1024

    def get_remaining_time_in_millis(self) -> int:
        return 10_000


@dataclass
class Response:
    status: int
    headers: dict
    body: dict


@pytest.fixture
def app(ddb_table, queues, qrypt_param, iam_guard, enforce_off, monkeypatch):
    """Fresh handler modules (so init-time config reads this test's env) plus the harness."""
    monkeypatch.setenv("MAX_RECEIVE_COUNT", "3")
    monkeypatch.setenv("RETRY_VISIBILITY_S", "5")
    monkeypatch.setenv("FAULT_INJECTION_ENABLED", "true")
    for name in [m for m in sys.modules if m.startswith(("qmc_api", "qmc_worker"))]:
        monkeypatch.delitem(sys.modules, name)
    api = importlib.import_module("qmc_api.handler")
    worker = importlib.import_module("qmc_worker.handler")
    qrypt = importlib.import_module("qmc_api.qrypt")
    source = qrypt.FakeEntropySource(seed=b"qmc-integration-v1")
    qrypt.set_entropy_source(source)
    yield Harness(api, worker, ddb_table, queues, iam_guard, source)
    qrypt.set_entropy_source(None)


@dataclass
class Harness:
    api: object
    worker: object
    table: object
    queues: dict
    iam_guard: object
    source: object

    # --- API -------------------------------------------------------------------------------
    def call(self, method: str, path: str, body: dict | None = None) -> Response:
        raw = json.dumps(body) if body is not None else None
        event = {
            "version": "2.0",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json"},
            "requestContext": {
                "http": {"method": method, "path": path, "sourceIp": "127.0.0.1"},
                "requestId": uuid.uuid4().hex,
            },
            "body": base64.b64encode(raw.encode()).decode() if raw else None,
            "isBase64Encoded": bool(raw),
            "pathParameters": None,
        }
        with self.iam_guard("api"):
            out = self.api.handle(event, Context())
        return Response(out["statusCode"], out.get("headers", {}), json.loads(out["body"]))

    def submit(self, body: dict = SMALL_JOB) -> str:
        r = self.call("POST", "/jobs", body)
        assert r.status == 202, r.body
        return r.body["job_id"]

    def get(self, job_id: str) -> dict:
        r = self.call("GET", f"/jobs/{job_id}")
        assert r.status == 200, r.body
        return r.body

    # --- SQS -> worker ---------------------------------------------------------------------
    def receive_all(self) -> list[dict]:
        """Every visible message on the main queue, as Lambda SQS records (each received once)."""
        import boto3

        sqs = boto3.client("sqs", region_name="us-east-2")
        records = []
        while True:
            msgs = sqs.receive_message(
                QueueUrl=self.queues["main"],
                MaxNumberOfMessages=10,
                AttributeNames=["All"],
            ).get("Messages", [])
            if not msgs:
                return records
            records += [
                {
                    "messageId": m["MessageId"],
                    "receiptHandle": m["ReceiptHandle"],
                    "body": m["Body"],
                    "attributes": m["Attributes"],
                    "messageAttributes": {},
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "arn:aws:sqs:us-east-2:123456789012:qmc-test-shards",
                    "awsRegion": "us-east-2",
                }
                for m in msgs
            ]

    def deliver(self, record: dict, attempt: int = 1) -> bool:
        """One worker invocation (batch size 1). True when the record was not reported failed."""
        rec = {
            **record,
            "attributes": {**record["attributes"], "ApproximateReceiveCount": str(attempt)},
        }
        with self.iam_guard("worker"):
            out = self.worker.handle({"Records": [rec]}, Context())
        return not (out or {}).get("batchItemFailures")

    def ack(self, record: dict) -> None:
        """What the event source mapping does after a successful invocation."""
        import boto3

        boto3.client("sqs", region_name="us-east-2").delete_message(
            QueueUrl=self.queues["main"], ReceiptHandle=record["receiptHandle"]
        )

    def run_all(self, records: list[dict]) -> None:
        for rec in records:
            assert self.deliver(rec), rec["body"]
            self.ack(rec)

    # --- DynamoDB (read outside the guard: the test, not a handler) -------------------------
    def job_item(self, job_id: str) -> dict:
        return self.table.get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"}, ConsistentRead=True)[
            "Item"
        ]

    def shard_items(self, job_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        return self.table.query(
            KeyConditionExpression=Key("pk").eq(f"JOB#{job_id}") & Key("sk").begins_with("SHARD#"),
            ConsistentRead=True,
        )["Items"]


def shard_index(record: dict) -> int:
    return json.loads(record["body"])["shard_index"]


def expected_estimate_hex(master_seed_hex: str, body: dict = SMALL_JOB) -> str:
    """The result a clean run must produce, computed directly from qmc_core."""
    from qmc_core.sim import simulate_shard

    m = master_int_from_hex(master_seed_hex)
    items = []
    for i, n in enumerate(split_paths(body["paths"], body["shards"])):
        r = simulate_shard(REFERENCE, n, m, i)
        items.append(
            {"shard_index": i, "n": r.n, "sum_hex": to_hex(r.sum), "sum_sq_hex": to_hex(r.sum_sq)}
        )
    return to_hex(aggregate(items).estimate)
