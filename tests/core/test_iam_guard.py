"""The IAM allow-list fixture fails on an unlisted operation (QMC-105)."""

import contextlib

import boto3
import pytest

from tests.conftest import IamViolation


def test_listed_operations_pass(ddb_table, iam_guard):
    with iam_guard("worker") as calls:
        ddb_table.put_item(Item={"pk": "a", "sk": "b"})
    assert ("dynamodb", "PutItem") in calls


def test_unlisted_operation_fails(ddb_table, iam_guard):
    with pytest.raises(IamViolation, match="GetItem"), iam_guard("worker"):
        ddb_table.get_item(Key={"pk": "a", "sk": "b"})


def test_swallowed_call_still_fails(qrypt_param, iam_guard):
    """Handlers catch everything, so the guard asserts at exit, not at call time."""
    with (
        pytest.raises(IamViolation, match="ssm"),
        iam_guard("worker"),
        contextlib.suppress(Exception),
    ):
        boto3.client("ssm", region_name="us-east-2").get_parameter(Name=qrypt_param)


def test_calls_outside_the_block_are_not_checked(ddb_table, iam_guard):
    with iam_guard("api"):
        pass
    ddb_table.scan()
