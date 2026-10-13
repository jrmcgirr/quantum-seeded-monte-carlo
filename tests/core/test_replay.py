from decimal import Decimal

from qmc_core.codec import to_hex
from qmc_core.replay import compare, job_fingerprint

FP = {
    "python": "3.13.1",
    "numpy": "2.5.4",
    "exp_target": "AVX2",
    "fma": True,
    "chunk_size": Decimal(131072),
    "cpu_model": "Intel Xeon A",
}


def shard(i, s, fp=FP):
    return {
        "shard_index": Decimal(i),
        "n": Decimal(100),
        "sum_hex": to_hex(s),
        "sum_sq_hex": to_hex(s * s),
        "fingerprint": fp,
    }


RESULT = {
    "n": Decimal(200),
    "estimate_hex": to_hex(1.5),
    "std_error_hex": to_hex(0.1),
    "ci95_hex": [to_hex(1.3), to_hex(1.7)],
}


def test_identical():
    a = [shard(0, 1.0), shard(1, 2.0)]
    b = [shard(1, 2.0, {**FP, "cpu_model": "AMD EPYC"}), shard(0, 1.0)]
    r = compare({"job_id": "o", "result": RESULT}, a, {"job_id": "r", "result": dict(RESULT)}, b)
    assert r == {
        "original_job_id": "o",
        "bit_identical": True,
        "estimate_equal": True,
        "shards_compared": 2,
        "shard_diffs": [],
        "fingerprint_match": True,
        "fingerprint_diffs": {},
    }


def test_one_ulp_change_flagged():
    import math

    a = [shard(0, 1.0), shard(1, 2.0)]
    b = [shard(0, 1.0), shard(1, math.nextafter(2.0, 3.0))]
    r = compare({"job_id": "o", "result": RESULT}, a, {"result": RESULT}, b)
    assert not r["bit_identical"]
    assert [d["field"] for d in r["shard_diffs"]] == ["sum_hex", "sum_sq_hex"]
    assert r["shard_diffs"][0]["original_hex"] == to_hex(2.0)


def test_missing_shard_result_diff_and_fingerprint_diff():
    a = [shard(0, 1.0), shard(1, 2.0)]
    b = [shard(0, 1.0, {**FP, "exp_target": "AVX512_SKX"})]
    r = compare({"result": RESULT}, a, {"result": {**RESULT, "estimate_hex": to_hex(1.6)}}, b)
    assert r["shard_diffs"] == [
        {"shard_index": 1, "field": "missing", "original_hex": "present", "replay_hex": None}
    ]
    assert not r["estimate_equal"] and not r["bit_identical"]
    assert r["fingerprint_diffs"] == {"0": {"exp_target": ["AVX2", "AVX512_SKX"]}}
    assert compare({}, a, {}, a)["bit_identical"] is False  # no results yet


def test_job_fingerprint():
    assert job_fingerprint([]) is None
    same = job_fingerprint([shard(0, 1.0), shard(1, 1.0, {**FP, "cpu_model": "other"})])
    assert same["exp_target"] == "AVX2" and "cpu_model" not in same and same["chunk_size"] == 131072
    mixed = job_fingerprint([shard(0, 1.0), shard(1, 1.0, {**FP, "fma": False})])
    assert mixed["mixed"] is True and [s["shard_index"] for s in mixed["by_shard"]] == [0, 1]
    assert mixed["by_shard"][1]["cpu_model"] == "Intel Xeon A"
