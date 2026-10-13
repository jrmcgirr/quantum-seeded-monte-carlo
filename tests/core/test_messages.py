import json
from dataclasses import replace
from decimal import Decimal

import pytest

from qmc_core import messages
from qmc_core.messages import MessageError, ShardMessage, batch_entries, decode, encode, for_job

JOB = "0123456789abcdef0123456789abcdef"
P = {"S0": "100.0", "K": "100.0", "r": "0.05", "sigma": "0.2", "T": "1.0"}
MSG = ShardMessage(
    job_id=JOB,
    shard_index=3,
    n_shards=10,
    shard_paths=100000,
    params=P,
    master_seed_hex="ab" * 32,
    chunk_size=131072,
    variance_reduction="none",
    algo_version="qmc-sim/v1",
)


def test_round_trip_and_shape():
    body = encode(MSG)
    assert " " not in body
    assert json.loads(body) == {
        "v": 1,
        "job_id": JOB,
        "shard_index": 3,
        "n_shards": 10,
        "shard_paths": 100000,
        "params": P,
        "master_seed_hex": "ab" * 32,
        "chunk_size": 131072,
        "variance_reduction": "none",
        "algo_version": "qmc-sim/v1",
        "fail": False,
    }
    assert decode(body) == MSG and decode(body.encode()) == MSG


def mutate(**changes):
    doc = json.loads(encode(MSG))
    for k, v in changes.items():
        if v is ...:
            doc.pop(k)
        else:
            doc[k] = v
    return json.dumps(doc)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[1]",
        b"\xff",
        mutate(v=2),
        mutate(v=True),
        mutate(v=...),
        mutate(extra=1),
        mutate(job_id="XYZ"),
        mutate(job_id=5),
        mutate(shard_index=10),
        mutate(shard_index=-1),
        mutate(shard_index=True),
        mutate(shard_index="3"),
        mutate(n_shards=0),
        mutate(n_shards=3.0),
        mutate(shard_paths=0),
        mutate(shard_paths=1e5),
        mutate(params={**P, "S0": 100.0}),
        mutate(params={**P, "S0": "nan"}),
        mutate(params={**P, "S0": "abc"}),
        mutate(params={"S0": "1"}),
        mutate(params="x"),
        mutate(master_seed_hex="ab" * 31),
        mutate(master_seed_hex=None),
        mutate(chunk_size=0),
        mutate(variance_reduction="sobol"),
        mutate(algo_version=""),
        mutate(fail="false"),
        mutate(fail=0),
    ],
)
def test_rejects_malformed(body):
    with pytest.raises(MessageError):
        decode(body)


def test_for_job_from_a_dynamodb_item():
    item = {
        "job_id": JOB,
        "paths_per_shard": [Decimal(4), Decimal(3), Decimal(3)],
        "params": P,
        "master_seed_hex": "ab" * 32,
        "chunk_size": Decimal(131072),
        "variance_reduction": "none",
        "algo_version": "qmc-sim/v1",
        "fault_injection": {"fail_shards": [Decimal(1)]},
    }
    msgs = for_job(item)
    assert [m.shard_paths for m in msgs] == [4, 3, 3] and [m.fail for m in msgs] == [
        False,
        True,
        False,
    ]
    assert all(m.n_shards == 3 and m.chunk_size == 131072 for m in msgs)
    for m in msgs:
        assert decode(encode(m)) == m
    assert not any(
        m.fail for m in for_job({k: v for k, v in item.items() if k != "fault_injection"})
    )


def test_batch_entries():
    msgs = [replace(MSG, shard_index=i, n_shards=23) for i in range(23)]
    batches = batch_entries(msgs)
    assert [len(b) for b in batches] == [10, 10, 3] and messages.BATCH_SIZE == 10
    assert batches[2][2]["Id"] == "22" and decode(batches[2][2]["MessageBody"]).shard_index == 22
    assert batch_entries([]) == []
