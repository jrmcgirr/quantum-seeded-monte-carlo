"""API test harness: moto table, queues and token parameter, the fake entropy source, and a
``call()`` that runs the handler inside WS0's IAM allow-list guard."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest


class Ctx:
    aws_request_id = "req-test-0001"

    def __init__(self, remaining_ms: int = 10_000) -> None:
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_ms


def event(method: str, path: str, body=None, raw: str | None = None, b64: bool = False) -> dict:
    text = raw if raw is not None else (json.dumps(body) if body is not None else None)
    if b64 and text is not None:
        text = base64.b64encode(text.encode()).decode()
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": {"authorization": "AWS4-HMAC-SHA256 Credential=SECRET-HEADER"},
        "requestContext": {"http": {"method": method, "path": path}},
        "body": text,
        "isBase64Encoded": b64,
    }


REF = {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 1000, "shards": 4}


@pytest.fixture
def api(ddb_table, queues, qrypt_param, iam_guard, monkeypatch):
    import boto3

    from qmc_api import aws, handler, qrypt, secret

    aws.reset()
    secret.clear()
    fake = qrypt.FakeEntropySource()
    qrypt.set_entropy_source(fake)
    sqs = boto3.client("sqs", region_name="us-east-2")

    def call(method, path, body=None, ctx=None, **kw):
        with iam_guard("api"):
            resp = handler.handle(event(method, path, body, **kw), ctx or Ctx())
        assert set(resp) == {"statusCode", "headers", "body"}
        assert resp["headers"]["x-request-id"] == Ctx.aws_request_id
        return resp["statusCode"], resp["headers"], json.loads(resp["body"])

    def items(prefix: str = "") -> list[dict]:
        return [i for i in ddb_table.scan()["Items"] if i["pk"].startswith(prefix)]

    def queued() -> int:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queues["main"], AttributeNames=["ApproximateNumberOfMessages"]
        )
        return int(attrs["Attributes"]["ApproximateNumberOfMessages"])

    def messages() -> list[dict]:
        out = []
        while True:
            got = sqs.receive_message(QueueUrl=queues["main"], MaxNumberOfMessages=10).get(
                "Messages", []
            )
            if not got:
                return out
            out += [json.loads(m["Body"]) for m in got]

    yield SimpleNamespace(
        call=call, table=ddb_table, fake=fake, items=items, queued=queued, messages=messages
    )
    qrypt.set_entropy_source(None)
    secret.clear()  # clients are reset at the next setup (a test may have patched aws.sqs)
