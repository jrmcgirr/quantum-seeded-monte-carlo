"""Per-job cost attribution (QMC-126, HANDOFF §7.10). Computed on read; never stored.

Formula (what tests check):

- ``lambda_gb_seconds`` = Σ over shards ``duration_ms / 1000 × memory_mb / 1024``
  + ``API_GB_S_PER_REQUEST`` × (1 + ``polls``)  (API: 512 MB × 0.25 s per request = 0.125 GB-s)
- ``lambda_requests`` = 1 (submit) + shards + ``polls``
- ``sqs_requests`` = ⌈shards / 10⌉ (SendMessageBatch) + shards (receive) + shards (delete)
- ``dynamodb_write_units`` = 5 (limiter, entropy, job, JOBLIST, finalize) + 2 × shards (shard put,
  mark_done) + 1 (margin)
- ``usd_at_list_price`` = GB-s × LAMBDA_USD_PER_GB_S + requests × LAMBDA_USD_PER_REQUEST
  + SQS × SQS_USD_PER_REQUEST + writes × DYNAMODB_USD_PER_WRITE (on-demand equivalent)
- ``usd_after_free_tier`` = 0.0 (always-free allowances cover it; COST.md has the arithmetic)

``polls`` is the number of ``GET /jobs/{id}`` calls the caller counted (0 if unknown).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

# us-east-2 list prices (2026-10).
# https://aws.amazon.com/lambda/pricing/ (x86, first tier)
LAMBDA_USD_PER_GB_S = 0.0000166667
LAMBDA_USD_PER_REQUEST = 0.20 / 1_000_000
# https://aws.amazon.com/sqs/pricing/ (standard queue)
SQS_USD_PER_REQUEST = 0.40 / 1_000_000
# https://aws.amazon.com/dynamodb/pricing/on-demand/ (write request unit)
DYNAMODB_USD_PER_WRITE = 0.625 / 1_000_000
API_GB_S_PER_REQUEST = 0.5 * 0.25


def estimate(job: Mapping, shards: Iterable[Mapping], polls: int = 0) -> dict | None:
    """``{"lambda_gb_seconds", "lambda_requests", "sqs_requests", "dynamodb_write_units",
    "usd_at_list_price", "usd_after_free_tier": 0.0}`` per the module formula; None unless the
    job is terminal (COMPLETE or PARTIAL)."""
    if job.get("status") not in ("COMPLETE", "PARTIAL"):
        return None
    n = int(job["shards"])
    worker_gb_s = math.fsum(
        int(s["duration_ms"]) / 1000 * int(s["memory_mb"]) / 1024 for s in shards
    )
    gb_s = worker_gb_s + API_GB_S_PER_REQUEST * (1 + polls)
    requests = 1 + n + polls
    sqs = math.ceil(n / 10) + 2 * n
    writes = 5 + 2 * n + 1
    usd = math.fsum(
        (
            gb_s * LAMBDA_USD_PER_GB_S,
            requests * LAMBDA_USD_PER_REQUEST,
            sqs * SQS_USD_PER_REQUEST,
            writes * DYNAMODB_USD_PER_WRITE,
        )
    )
    return {
        "lambda_gb_seconds": gb_s,
        "lambda_requests": requests,
        "sqs_requests": sqs,
        "dynamodb_write_units": writes,
        "usd_at_list_price": usd,
        "usd_after_free_tier": 0.0,
    }
