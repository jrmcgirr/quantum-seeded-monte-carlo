import math
import random
import struct
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qmc_core.aggregate import Z95, Result, aggregate, decide
from qmc_core.codec import from_hex, iso_ms, ns_to_set, params_from_repr, params_to_repr, to_hex


def test_hex_round_trip_100k_including_edge_cases():
    rnd = random.Random(1)
    values = [
        0.0,
        -0.0,
        5e-324,
        -5e-324,
        2.2250738585072014e-308,
        math.inf,
        -math.inf,
        1.7976931348623157e308,
    ]
    values += [
        struct.unpack("<d", rnd.getrandbits(64).to_bytes(8, "little"))[0] for _ in range(100_000)
    ]
    for x in values:
        if math.isnan(x):
            continue
        y = from_hex(to_hex(x))
        assert struct.pack("<d", x) == struct.pack("<d", y)


@given(st.floats(allow_nan=False))
def test_hex_round_trip_property(x):
    assert struct.pack("<d", from_hex(to_hex(x))) == struct.pack("<d", x)


def test_params_repr_round_trip():
    p = {"S0": 100, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1}
    r = params_to_repr(p)
    assert r == {"S0": "100.0", "K": "100.0", "r": "0.05", "sigma": "0.2", "T": "1.0"}
    assert params_from_repr(r) == {k: float(v) for k, v in p.items()}


def test_ns_to_set_and_iso():
    assert ns_to_set(None) == frozenset() and ns_to_set(set()) == frozenset()
    assert ns_to_set({Decimal(1), Decimal(3)}) == {1, 3}
    assert iso_ms(0) == "1970-01-01T00:00:00.000Z"
    assert iso_ms(1792000267.4129) == "2026-10-14T17:51:07.412Z"


def shard(i, xs):
    return {
        "shard_index": Decimal(i),
        "n": Decimal(len(xs)),
        "sum_hex": to_hex(math.fsum(xs)),
        "sum_sq_hex": to_hex(math.fsum(x * x for x in xs)),
    }


def test_aggregate_matches_reference():
    rnd = random.Random(7)
    groups = [[rnd.expovariate(0.1) for _ in range(rnd.randint(1, 500))] for _ in range(12)]
    res = aggregate([shard(i, g) for i, g in enumerate(groups)])
    flat = [x for g in groups for x in g]
    n = len(flat)
    mean = sum(Fraction(x) for x in flat) / n
    var = sum((Fraction(x) - mean) ** 2 for x in flat) / (n - 1)
    assert res.n == n
    assert math.isclose(res.estimate, float(mean), rel_tol=1e-12)
    assert math.isclose(res.std_error, math.sqrt(float(var) / n), rel_tol=1e-9)
    assert res.ci95 == (res.estimate - Z95 * res.std_error, res.estimate + Z95 * res.std_error)


def test_aggregate_is_order_independent_bitwise():
    rnd = random.Random(3)
    shards = [shard(i, [rnd.random() * 1e3 for _ in range(50)]) for i in range(30)]
    expected = aggregate(shards).to_item()
    for _ in range(20):
        rnd.shuffle(shards)
        assert aggregate(shards).to_item() == expected


def test_aggregate_empty_one_and_only():
    assert aggregate([]) is None
    assert aggregate([shard(0, [])]) is None
    one = aggregate([shard(0, [4.5])])
    assert one == Result(n=1, estimate=4.5, std_error=None, ci95=None)
    assert one.to_item() == {
        "n": 1,
        "estimate_hex": to_hex(4.5),
        "std_error_hex": None,
        "ci95_hex": None,
    }
    shards = [shard(0, [1.0, 2.0]), shard(1, [10.0, 20.0]), shard(2, [3.0])]
    assert aggregate(shards, only=[0, 2]).n == 3
    assert aggregate(shards, only=[]) is None


def test_aggregate_constant_values_never_negative_variance():
    res = aggregate([shard(0, [0.1] * 1000), shard(1, [0.1] * 1000)])
    assert res.std_error >= 0.0


def test_result_item_and_view_round_trip():
    res = aggregate([shard(0, [1.0, 2.0, 4.0])])
    item = res.to_item()
    assert all(
        isinstance(v, str) for v in (item["estimate_hex"], item["std_error_hex"], *item["ci95_hex"])
    )
    assert Result.from_item({**item, "n": Decimal(item["n"])}) == res
    view = res.to_view()
    assert view["estimate"] == res.estimate and view["estimate_hex"] == item["estimate_hex"]
    assert view["ci95"] == list(res.ci95) and view["n"] == 3
    one = Result(1, 2.0, None, None)
    assert Result.from_item(one.to_item()) == one and one.to_view()["ci95"] is None


def job(status="QUEUED", shards=4, done=None, failed=None, deadline=1000):
    j = {"status": status, "shards": Decimal(shards), "deadline_at": Decimal(deadline)}
    if done:
        j["done_shards"] = {Decimal(i) for i in done}
    if failed:
        j["failed_shards"] = {Decimal(i) for i in failed}
    return j


def test_decide_complete():
    d = decide(job(done=[0, 1, 2, 3]))
    assert (d.action, d.n_done_seen, d.add_failed, d.reason) == (
        "complete",
        4,
        frozenset(),
        "all_accounted",
    )


def test_decide_partial_when_all_accounted():
    d = decide(job(done=[0, 1, 3], failed=[2]))
    assert (d.action, d.failed, d.add_failed, d.n_done_seen) == ("partial", {2}, frozenset(), 3)


def test_decide_failed_then_done_is_still_partial():
    d = decide(job(done=[0, 1, 2, 3], failed=[2]))
    assert d.action == "partial" and d.failed == frozenset()


def test_decide_not_yet():
    assert decide(job(done=[0, 1])).action == "none"
    assert decide(job(done=[0, 1]), now_epoch=999).action == "none"
    assert decide(job(done=[0, 1]), now_epoch=1000).action == "none"


def test_decide_reaped_at_deadline():
    d = decide(job(done=[0], failed=[2]), now_epoch=1001)
    assert (d.action, d.reason) == ("partial", "deadline")
    assert d.failed == {1, 2, 3} and d.add_failed == {1, 3} and d.n_done_seen == 1


def test_decide_reaped_with_zero_done():
    d = decide(job(), now_epoch=5000)
    assert d.action == "partial" and d.n_done_seen == 0 and d.add_failed == {0, 1, 2, 3}


@pytest.mark.parametrize("status", ["COMPLETE", "PARTIAL", "FAILED"])
def test_decide_already_terminal(status):
    assert decide(job(status, done=[0, 1, 2, 3]), now_epoch=10**9).action == "none"
