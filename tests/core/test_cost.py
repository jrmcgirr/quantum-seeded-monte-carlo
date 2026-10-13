import math
from decimal import Decimal

import pytest

from qmc_core import cost
from qmc_core.errors import RETRYABLE_ENTROPY_CODES, ErrorCode


def shard(ms):
    return {"duration_ms": Decimal(ms), "memory_mb": Decimal(1024)}


def test_estimate_formula():
    job = {"status": "COMPLETE", "shards": Decimal(10)}
    got = cost.estimate(job, [shard(50)] * 10, polls=3)
    gb_s = 10 * 0.05 + cost.API_GB_S_PER_REQUEST * 4
    assert got["lambda_gb_seconds"] == pytest.approx(gb_s)
    assert (got["lambda_requests"], got["sqs_requests"], got["dynamodb_write_units"]) == (
        14,
        21,
        26,
    )
    usd = (
        gb_s * cost.LAMBDA_USD_PER_GB_S
        + 14 * cost.LAMBDA_USD_PER_REQUEST
        + 21 * cost.SQS_USD_PER_REQUEST
        + 26 * cost.DYNAMODB_USD_PER_WRITE
    )
    assert math.isclose(got["usd_at_list_price"], usd, rel_tol=1e-12)
    assert got["usd_after_free_tier"] == 0.0
    assert not any(isinstance(v, Decimal) for v in got.values())


def test_estimate_partial_and_not_terminal():
    assert cost.estimate({"status": "PARTIAL", "shards": 3}, [shard(10)] * 2)["sqs_requests"] == 7
    for status in ("QUEUED", "PENDING_ENTROPY", "FAILED"):
        assert cost.estimate({"status": status, "shards": 3}, []) is None


def test_retryable_entropy_codes():
    assert ErrorCode.QRYPT_FORBIDDEN not in RETRYABLE_ENTROPY_CODES
    assert ErrorCode.SECRET_UNAVAILABLE not in RETRYABLE_ENTROPY_CODES
    assert ErrorCode.QRYPT_TIMEOUT in RETRYABLE_ENTROPY_CODES and len(RETRYABLE_ENTROPY_CODES) == 6
