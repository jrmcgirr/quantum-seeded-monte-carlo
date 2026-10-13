"""Every §6.2 builder executed against moto DynamoDB (QMC-105)."""

import re
from decimal import Decimal

import boto3
import pytest
from botocore.exceptions import ClientError

from qmc_core import ddb
from qmc_core.aggregate import Result, ShardResult, aggregate, decide
from qmc_core.codec import params_to_repr

JOB = "0123456789abcdef0123456789abcdef"
NOW = 1_792_000_000.0
FP = {"python": "3.13.1", "exp_target": "AVX2", "fma": True, "cpu_model": None}
RESERVED = {"status", "ttl", "result", "size", "partial", "n"}


def new_job(shards=3, **kw):
    return ddb.new_job_item(
        job_id=JOB,
        params=params_to_repr({"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1}),
        paths=10 * shards,
        shards=shards,
        paths_per_shard=[10] * shards,
        chunk_size=131072,
        variance_reduction="none",
        algo_version="qmc-sim/v1",
        master_seed_hex="ab" * 32,
        seed_provenance={"source": "qrypt", "latency_ms": 212, "raw_sha256": "cd" * 32},
        now_epoch=NOW,
        deadline_s=900,
        **kw,
    )


def put_shard(table, i, s=1.0):
    table.put_item(
        **ddb.shard_put(
            JOB,
            i,
            ShardResult(10, s, s * s),
            duration_ms=5,
            memory_mb=1024,
            attempt=1,
            fingerprint=FP,
            now_epoch=NOW,
        )
    )


def refused(fn):
    with pytest.raises(ClientError) as e:
        fn()
    assert ddb.is_conditional_failure(e.value)
    return e.value


def all_builders():
    r = Result(2, 1.0, 0.5, (0.0, 2.0))
    return [
        ddb.limiter_take(1_792_000_000, 15, NOW),
        ddb.limiter_gap_take(NOW, 2000),
        ddb.entropy_claim("cd" * 32, JOB, NOW),
        ddb.job_put(new_job()),
        ddb.joblist_put(new_job()),
        ddb.shard_put(
            JOB,
            0,
            ShardResult(1, 1.0, 1.0),
            duration_ms=1,
            memory_mb=1,
            attempt=1,
            fingerprint=FP,
            now_epoch=NOW,
        ),
        ddb.mark_done(JOB, 0),
        ddb.mark_failed(JOB, 0),
        ddb.finalize_complete(JOB, r, NOW),
        ddb.finalize_partial(JOB, r, [1], 2, NOW),
        ddb.finalize_partial(JOB, None, [], 0, NOW),
        ddb.mark_enqueue_failed(JOB, "x"),
        ddb.shards_query(JOB),
        ddb.job_get(JOB),
        ddb.joblist_query(),
        ddb.health_get(),
    ]


def test_expressions_name_every_attribute_and_carry_no_floats():
    def walk(v):
        assert not isinstance(v, float), v
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list | set | tuple):
            for x in v:
                walk(x)

    for kw in all_builders():
        walk(kw)
        text = " ".join(
            kw.get(k, "")
            for k in (
                "UpdateExpression",
                "ConditionExpression",
                "KeyConditionExpression",
                "ProjectionExpression",
            )
        )
        bare = set(re.findall(r"(?<![#:\w])([a-z_]+)\b", text)) - {
            "ADD",
            "SET",
            "AND",
            "OR",
            "NOT",
            "attribute_exists",
            "attribute_not_exists",
            "contains",
            "size",
            "begins_with",
        }
        assert not bare, f"attribute names not via ExpressionAttributeNames: {bare} in {text}"
        names = kw.get("ExpressionAttributeNames", {})
        for placeholder in names:
            assert placeholder in text, f"unused name {placeholder}"
        for placeholder in kw.get("ExpressionAttributeValues", {}):
            assert placeholder in text, f"unused value {placeholder}"


def test_keys():
    assert ddb.job_key(JOB) == {"pk": f"JOB#{JOB}", "sk": "META"}
    assert ddb.shard_key(JOB, 3) == {"pk": f"JOB#{JOB}", "sk": "SHARD#0003"}
    assert ddb.limiter_window(1_792_000_007.9) == 1_792_000_007
    assert ddb.limiter_window(1_792_000_007.9, 10) == 1_792_000_000


def test_job_put_and_get(ddb_table):
    item = new_job(replay_of="f" * 32, fault_injection={"fail_shards": [1]})
    ddb_table.put_item(**ddb.job_put(item))
    got = ddb_table.get_item(**ddb.job_get(JOB))["Item"]
    assert got["status"] == "QUEUED" and got["deadline_at"] == int(NOW) + 900
    assert got["ttl"] == int(NOW) + 365 * 86400 and got["created_at"].endswith("Z")
    assert got["replay_of"] == "f" * 32 and got["fault_injection"] == {"fail_shards": [1]}
    assert "done_shards" not in got and "failed_shards" not in got
    refused(lambda: ddb_table.put_item(**ddb.job_put(item)))
    assert "replay_of" not in new_job() and "fault_injection" not in new_job()


def test_joblist_newest_first_and_statuses(ddb_table):
    for k, t in enumerate([NOW, NOW + 1, NOW + 2]):
        jid = f"{k:032x}"
        item = {**new_job(), **ddb.job_key(jid), "job_id": jid}
        item["created_at"] = ddb.new_job_item(**{**_args(), "now_epoch": t})["created_at"]
        if k == 2:
            item["replay_of"] = f"{0:032x}"
        ddb_table.put_item(**ddb.job_put(item))
        ddb_table.put_item(**ddb.joblist_put(item))
    rows = ddb_table.query(**ddb.joblist_query(limit=2))["Items"]
    assert [r["job_id"] for r in rows] == [f"{2:032x}", f"{1:032x}"]
    assert rows[0]["replay_of"] == f"{0:032x}" and "replay_of" not in rows[1]
    res = boto3.resource("dynamodb", region_name="us-east-2").batch_get_item(
        **ddb.job_statuses(ddb_table.name, [r["job_id"] for r in rows] * 2)
    )
    got = res["Responses"][ddb_table.name]
    assert sorted(g["job_id"] for g in got) == [f"{1:032x}", f"{2:032x}"]
    assert all(set(g) == {"job_id", "status"} for g in got)
    with pytest.raises(ValueError):
        ddb.job_statuses("t", [f"{i:032x}" for i in range(101)])


def _args():
    return dict(
        job_id=JOB,
        params={},
        paths=1,
        shards=1,
        paths_per_shard=[1],
        chunk_size=1,
        variance_reduction="none",
        algo_version="v",
        master_seed_hex="",
        seed_provenance={},
        now_epoch=NOW,
        deadline_s=1,
    )


def test_limiter_caps_window(ddb_table):
    w = ddb.limiter_window(NOW)
    for _ in range(3):
        ddb_table.update_item(**ddb.limiter_take(w, 3, NOW))
    refused(lambda: ddb_table.update_item(**ddb.limiter_take(w, 3, NOW)))
    ddb_table.update_item(**ddb.limiter_take(w + 1, 3, NOW + 1))
    item = ddb_table.get_item(Key={"pk": "RL#qrypt", "sk": str(w)})["Item"]
    assert item["n"] == 3 and item["ttl"] == w + 1 + 3600


def test_limiter_one_per_second(ddb_table):
    """Contract change 005: 1 s windows, cap 1. A second call in the same second is refused."""
    w = ddb.limiter_window(NOW + 0.2)
    ddb_table.update_item(**ddb.limiter_take(w, 1, NOW + 0.2))
    refused(
        lambda: ddb_table.update_item(
            **ddb.limiter_take(ddb.limiter_window(NOW + 0.9), 1, NOW + 0.9)
        )
    )
    ddb_table.update_item(**ddb.limiter_take(ddb.limiter_window(NOW + 1.0), 1, NOW + 1.0))


def test_limiter_ten_second_window_still_supported(ddb_table):
    w = ddb.limiter_window(NOW, 10)
    ddb_table.update_item(**ddb.limiter_take(w, 1, NOW, 10))
    item = ddb_table.get_item(Key={"pk": "RL#qrypt", "sk": str(w)})["Item"]
    assert item["ttl"] == w + 10 + 3600


def test_entropy_claim_once(ddb_table):
    ddb_table.put_item(**ddb.entropy_claim("cd" * 32, JOB, NOW))
    refused(lambda: ddb_table.put_item(**ddb.entropy_claim("cd" * 32, "f" * 32, NOW)))


def test_duplicate_shard_put_rejected_and_first_wins(ddb_table):
    put_shard(ddb_table, 1, 2.0)
    refused(lambda: put_shard(ddb_table, 1, 3.0))
    items = ddb_table.query(**ddb.shards_query(JOB))["Items"]
    assert len(items) == 1 and items[0]["sum_hex"] == (2.0).hex() and items[0]["shard_index"] == 1
    assert aggregate(items).estimate == 0.2


def test_mark_done_idempotent_and_orphan(ddb_table):
    refused(lambda: ddb_table.update_item(**ddb.mark_done(JOB, 0)))  # no job item: orphan
    ddb_table.put_item(**ddb.job_put(new_job()))
    for _ in range(3):
        out = ddb_table.update_item(**ddb.mark_done(JOB, 0))["Attributes"]
    assert out["done_shards"] == {0} and out["status"] == "QUEUED"


def test_mark_failed_refuses_done_shard_and_returns_item(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    out = ddb_table.update_item(**ddb.mark_failed(JOB, 2))["Attributes"]  # no done set yet
    assert out["failed_shards"] == {2}
    ddb_table.update_item(**ddb.mark_done(JOB, 1))
    err = refused(lambda: ddb_table.update_item(**ddb.mark_failed(JOB, 1)))
    old = ddb.item_from_error(err)
    assert old["done_shards"] == {1} and old["failed_shards"] == {2} and old["status"] == "QUEUED"
    out = ddb_table.update_item(**ddb.mark_failed(JOB, 0))["Attributes"]
    assert out["failed_shards"] == {0, 2}


def test_finalize_complete_once(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    for i in range(3):
        put_shard(ddb_table, i, float(i))
        job = ddb_table.update_item(**ddb.mark_done(JOB, i))["Attributes"]
    d = decide(job)
    assert d.action == "complete"
    res = aggregate(ddb_table.query(**ddb.shards_query(JOB))["Items"])
    ddb_table.update_item(**ddb.finalize_complete(JOB, res, NOW))
    err = refused(lambda: ddb_table.update_item(**ddb.finalize_complete(JOB, res, NOW)))
    assert ddb.item_from_error(err)["status"] == "COMPLETE"
    got = ddb_table.get_item(**ddb.job_get(JOB))["Item"]
    assert got["result"] == res.to_item() and Result.from_item(got["result"]) == res
    assert got["completed_at"].endswith("Z")
    refused(lambda: ddb_table.update_item(**ddb.finalize_partial(JOB, None, [0], 3, NOW)))


def test_complete_refused_after_a_failure(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    ddb_table.update_item(**ddb.mark_failed(JOB, 2))
    refused(
        lambda: ddb_table.update_item(**ddb.finalize_complete(JOB, Result(1, 1.0, None, None), NOW))
    )


def test_finalize_partial_snapshot(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    ddb_table.update_item(**ddb.mark_done(JOB, 0))
    stale = decide(ddb_table.get_item(**ddb.job_get(JOB))["Item"], now_epoch=NOW + 901)
    assert stale.action == "partial" and stale.n_done_seen == 1
    ddb_table.update_item(**ddb.mark_done(JOB, 1))  # a shard lands after the read
    err = refused(
        lambda: ddb_table.update_item(
            **ddb.finalize_partial(JOB, None, stale.add_failed, stale.n_done_seen, NOW)
        )
    )
    fresh = decide(ddb.item_from_error(err), now_epoch=NOW + 901)
    assert fresh.n_done_seen == 2 and fresh.add_failed == {2}
    put_shard(ddb_table, 0)
    put_shard(ddb_table, 1)
    partial = aggregate(ddb_table.query(**ddb.shards_query(JOB))["Items"], only=fresh.done)
    ddb_table.update_item(
        **ddb.finalize_partial(JOB, partial, fresh.add_failed, fresh.n_done_seen, NOW)
    )
    got = ddb_table.get_item(**ddb.job_get(JOB))["Item"]
    assert got["status"] == "PARTIAL" and got["failed_shards"] == {2}
    assert got["partial_result"]["n"] == 20
    ddb_table.update_item(**ddb.mark_done(JOB, 2))  # late shard: status stays PARTIAL
    assert decide(ddb_table.get_item(**ddb.job_get(JOB))["Item"]).action == "none"


def test_finalize_partial_zero_done_and_empty_add(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    kw = ddb.finalize_partial(JOB, None, [], 0, NOW)
    assert (
        "ADD" not in kw["UpdateExpression"]
        and "attribute_not_exists(#done)" in kw["ConditionExpression"]
    )
    ddb_table.update_item(**kw)
    got = ddb_table.get_item(**ddb.job_get(JOB))["Item"]
    assert (
        got["status"] == "PARTIAL" and got["partial_result"] is None and "failed_shards" not in got
    )


def test_finalize_partial_zero_done_refused_once_a_shard_lands(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    ddb_table.update_item(**ddb.mark_done(JOB, 0))
    refused(lambda: ddb_table.update_item(**ddb.finalize_partial(JOB, None, [0, 1, 2], 0, NOW)))


def test_mark_enqueue_failed(ddb_table):
    ddb_table.put_item(**ddb.job_put(new_job()))
    ddb_table.update_item(**ddb.mark_enqueue_failed(JOB, "SendMessageBatch failed"))
    got = ddb_table.get_item(**ddb.job_get(JOB))["Item"]
    assert (got["status"], got["fail_reason"]) == ("FAILED", "SendMessageBatch failed")
    refused(lambda: ddb_table.update_item(**ddb.mark_enqueue_failed(JOB, "again")))


def test_shards_query_index_order(ddb_table):
    for i in (10, 2, 0):
        put_shard(ddb_table, i)
    ddb_table.put_item(**ddb.job_put(new_job()))
    items = ddb_table.query(**ddb.shards_query(JOB))["Items"]
    assert [int(i["shard_index"]) for i in items] == [0, 2, 10]
    assert items[0]["fingerprint"] == FP and items[0]["n"] == Decimal(10)


def test_health_get(ddb_table):
    assert "Item" not in ddb_table.get_item(**ddb.health_get())


def test_error_helpers():
    assert not ddb.is_conditional_failure(ValueError())
    assert ddb.item_from_error(ValueError()) is None
    e = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
    assert ddb.is_conditional_failure(e) and ddb.item_from_error(e) is None


def test_limiter_gap_spaces_calls(ddb_table):
    """Contract change 006: two Qrypt calls are always >= gap_ms apart, race-free."""
    ddb_table.update_item(**ddb.limiter_gap_take(NOW, 2000))
    err = refused(lambda: ddb_table.update_item(**ddb.limiter_gap_take(NOW + 1.999, 2000)))
    last = ddb.item_from_error(err)
    assert last["at_ms"] == int(NOW * 1000)
    assert ddb.limiter_gap_retry_after(last, NOW + 1.999, 2000) == 1
    assert ddb.limiter_gap_retry_after(last, NOW + 0.2, 2000) == 2
    ddb_table.update_item(**ddb.limiter_gap_take(NOW + 2.0, 2000))
    item = ddb_table.get_item(Key=ddb.LIMITER_GAP_KEY)["Item"]
    assert item["at_ms"] == int((NOW + 2.0) * 1000) and item["ttl"] == int(NOW + 2.0) + 3600


def test_limiter_gap_retry_after_without_item():
    assert ddb.limiter_gap_retry_after(None, NOW, 2000) == 1
    assert ddb.limiter_gap_retry_after({}, NOW, 2000) == 1
