"""Worker test helpers: jobs on the moto table, Lambda-shaped SQS records, stdout capture.

Runs on WS0's fixtures (tests/conftest.py): ddb_table, queues, enforce_off, iam_guard.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from qmc_core import aggregate, codec, ddb, messages, params, seeds, sim

REF_PARAMS = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}


@pytest.fixture
def worker(enforce_off, ddb_table, queues):
    """The handler module, imported with the self-check in warn mode, boto3 caches reset per test."""
    mod = importlib.import_module("qmc_worker.handler")
    mod._table_for.cache_clear()
    mod._sqs.cache_clear()
    return mod


@pytest.fixture
def ctx():
    return SimpleNamespace(aws_request_id="req-test", memory_limit_in_mb="1024")


@pytest.fixture
def make_job(ddb_table):
    """Put a QUEUED job item; return ``(item, [ShardMessage, ...])``."""

    def make(
        paths: int = 2000,
        shards: int = 10,
        fail_shards: list[int] | None = None,
        variance_reduction: str = "none",
    ):
        job_id = uuid.uuid4().hex
        item = ddb.new_job_item(
            job_id=job_id,
            params=codec.params_to_repr(REF_PARAMS),
            paths=paths,
            shards=shards,
            paths_per_shard=params.split_paths(paths, shards, variance_reduction),
            chunk_size=sim.CHUNK_SIZE,
            variance_reduction=variance_reduction,
            algo_version=sim.ALGO_VERSION,
            # A fixed test seed: tests never touch Qrypt (CLAUDE.md, FakeEntropy rule).
            master_seed_hex=hashlib.sha256(job_id.encode()).hexdigest(),
            seed_provenance={"source": "test"},
            now_epoch=time.time(),
            deadline_s=900,
            fault_injection={"fail_shards": fail_shards} if fail_shards else None,
        )
        ddb_table.put_item(**ddb.job_put(item))
        return item, messages.for_job(item)

    return make


def record(msg: messages.ShardMessage | str, attempt: int = 1, receipt: str = "rh") -> dict:
    """A Lambda SQS record without a real queue (fine for paths that never touch SQS)."""
    body = msg if isinstance(msg, str) else messages.encode(msg)
    return {
        "messageId": str(uuid.uuid4()),
        "receiptHandle": receipt,
        "body": body,
        "attributes": {"ApproximateReceiveCount": str(attempt)},
        "eventSource": "aws:sqs",
    }


def event(*records: dict) -> dict:
    return {"Records": list(records)}


def job_item(table, job_id: str) -> dict:
    return table.get_item(Key=ddb.job_key(job_id), ConsistentRead=True)["Item"]


def expected_result(item: dict) -> dict:
    """The job result computed directly (no worker, no SQS) through qmc_core."""
    master = seeds.master_int_from_hex(item["master_seed_hex"])
    shards = []
    for i, n in enumerate(item["paths_per_shard"]):
        r = sim.simulate_shard(
            REF_PARAMS, int(n), master, i, variance_reduction=item["variance_reduction"]
        )
        shards.append(
            ddb.shard_put(
                item["job_id"],
                i,
                r,
                duration_ms=0,
                memory_mb=0,
                attempt=1,
                fingerprint={},
                now_epoch=0,
            )["Item"]
        )
    return aggregate.aggregate(shards).to_item()


def stdout_lines(capsys) -> list[dict]:
    """Every stdout line as JSON (also asserts nothing non-JSON was written)."""
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def metric_total(lines: list[dict], name: str) -> int:
    return sum(int(o[name]) for o in lines if "_aws" in o and name in o)


def events(lines: list[dict], name: str) -> list[dict]:
    return [o for o in lines if o.get("event") == name]
