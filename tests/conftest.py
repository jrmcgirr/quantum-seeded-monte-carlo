"""Shared fixtures (WS0): moto table and queues per HANDOFF §6.1/§8.2, and the IAM allow-list guard.

Every moto test in tests/api, tests/worker and tests/integration should run handler code inside
``iam_guard("api")`` or ``iam_guard("worker")``: it records every AWS operation and fails the test
if one is outside that function's HANDOFF §8.3 allow-list (a missing IAM permission fails in CI,
not as AccessDenied on Lambda).
"""

from __future__ import annotations

import contextlib

import pytest

TABLE_NAME = "qmc-test"
QRYPT_PARAM = "/qmc/qrypt-api-key"

# HANDOFF §8.3, as API operations (an IAM action covers its *Batch variant).
ALLOWED_OPERATIONS: dict[str, frozenset[tuple[str, str]]] = {
    "api": frozenset(
        {("dynamodb", op) for op in ("GetItem", "BatchGetItem", "PutItem", "UpdateItem", "Query")}
        | {("sqs", "SendMessage"), ("sqs", "SendMessageBatch"), ("ssm", "GetParameter")}
        # Contract changes 007 (optional watcher) and 008 (PENDING_ENTROPY self-invoke).
        | {("states", "StartExecution"), ("lambda", "Invoke")}
    ),
    "worker": frozenset(
        {("dynamodb", op) for op in ("PutItem", "UpdateItem", "Query")}
        | {
            ("sqs", op)
            for op in (
                "ReceiveMessage",
                "DeleteMessage",
                "DeleteMessageBatch",
                "GetQueueAttributes",
                "ChangeMessageVisibility",
                "ChangeMessageVisibilityBatch",
            )
        }
    ),
}


class IamViolation(AssertionError):
    pass


@pytest.fixture
def aws_env(monkeypatch):
    """Fake credentials and the §8.4 environment, so nothing can reach a real account."""
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-2",
        "AWS_REGION": "us-east-2",
        "QMC_ENV": "test",
        "QMC_BUILD_SHA": "test",
        "TABLE_NAME": TABLE_NAME,
        "METRICS_NAMESPACE": "QMC/test",
        "QRYPT_API_KEY_PARAM": QRYPT_PARAM,
        "FAULT_INJECTION_ENABLED": "false",
    }
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


@pytest.fixture
def moto_aws(aws_env):
    from moto import mock_aws

    with mock_aws():
        yield


@pytest.fixture
def ddb_table(moto_aws):
    """The §6.1 table (pk/sk strings, PROVISIONED 10/10) as a boto3 resource Table."""
    import boto3

    ddb = boto3.resource("dynamodb", region_name="us-east-2")
    table = ddb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 10, "WriteCapacityUnits": 10},
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def queues(moto_aws, monkeypatch):
    """Main queue + DLQ with the §8.2 redrive (maxReceiveCount 3). Sets QUEUE_URL."""
    import json

    import boto3

    sqs = boto3.client("sqs", region_name="us-east-2")
    dlq = sqs.create_queue(QueueName="qmc-test-shards-dlq")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    main = sqs.create_queue(
        QueueName="qmc-test-shards",
        Attributes={
            "VisibilityTimeout": "60",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 3}),
        },
    )["QueueUrl"]
    monkeypatch.setenv("QUEUE_URL", main)
    return {"main": main, "dlq": dlq}


@pytest.fixture
def qrypt_param(moto_aws):
    """A SecureString at QRYPT_API_KEY_PARAM with a dummy value (no test needs a real token)."""
    import boto3

    boto3.client("ssm", region_name="us-east-2").put_parameter(
        Name=QRYPT_PARAM, Value="dummy-not-a-token", Type="SecureString"
    )
    return QRYPT_PARAM


@pytest.fixture
def iam_guard(monkeypatch):
    """``with iam_guard("worker") as calls: handle(event, ctx)``.

    Records ``(service, operation)`` for every botocore call made inside the block and raises
    ``IamViolation`` at exit if any is outside ``ALLOWED_OPERATIONS[fn]``. Recording (rather than
    raising at call time) matters because handlers catch every exception.
    """
    from botocore.client import BaseClient

    active: list[list[tuple[str, str]]] = []
    original = BaseClient._make_api_call

    def recording(self, operation_name, api_params):
        if active:
            active[-1].append((self.meta.service_model.endpoint_prefix, operation_name))
        return original(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", recording)

    @contextlib.contextmanager
    def guard(fn: str):
        allowed = ALLOWED_OPERATIONS[fn]
        calls: list[tuple[str, str]] = []
        active.append(calls)
        try:
            yield calls
        finally:
            active.pop()
        bad = sorted({c for c in calls if c not in allowed})
        if bad:
            raise IamViolation(f"{fn} called operations outside HANDOFF §8.3: {bad}")

    return guard


@pytest.fixture
def enforce_off(monkeypatch):
    """Worker tests import the handler on any CPU: the SIMD self-check only warns."""
    monkeypatch.setenv("QMC_ENFORCE_PINNED_DISPATCH", "false")
