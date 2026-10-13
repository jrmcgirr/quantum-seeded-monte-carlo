import math

import numpy as np
import pytest

from qmc_core import fingerprint as fpm
from qmc_core.aggregate import aggregate
from qmc_core.bs import black_scholes_call, reference
from qmc_core.codec import to_hex
from qmc_core.seeds import derive_master_seed, shard_generator
from qmc_core.sim import CHUNK_SIZE, simulate_shard

P = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}
M = derive_master_seed(bytes(range(256)) * 4, "0123456789abcdef0123456789abcdef").int


def test_black_scholes_reference():
    assert abs(black_scholes_call(100.0, 100.0, 0.05, 0.2, 1.0) - 10.450583572185565) <= 1e-12


def test_reference_block():
    ref = reference(P, 10.45, 0.01)
    assert ref["black_scholes"] == black_scholes_call(**P)
    assert (
        math.isclose(ref["z_score"], (10.45 - ref["black_scholes"]) / 0.01)
        and ref["within_3se"] is True
    )
    assert reference(P, 11.0, 0.01)["within_3se"] is False
    assert reference(P, 11.0, None) == {
        "black_scholes": ref["black_scholes"],
        "z_score": None,
        "within_3se": None,
    }


def test_chunk_size_is_2_17():
    assert CHUNK_SIZE == 2**17


def test_deterministic_across_three_chunks():
    n = 2 * CHUNK_SIZE + 1000
    a = simulate_shard(P, n, M, 4)
    b = simulate_shard(P, n, M, 4)
    assert (to_hex(a.sum), to_hex(a.sum_sq), a.n) == (to_hex(b.sum), to_hex(b.sum_sq), n)
    assert to_hex(simulate_shard(P, n, M, 5).sum) != to_hex(a.sum)


def test_matches_spec_operation_order():
    """Recompute §7.3 literally, chunk by chunk, and require identical bits."""
    n, chunk = 25_000, 10_000
    rng = shard_generator(M, 2)
    drift = (P["r"] - 0.5 * P["sigma"] * P["sigma"]) * P["T"]
    vol = P["sigma"] * math.sqrt(P["T"])
    disc = math.exp(-P["r"] * P["T"])
    sums, sumsq = [], []
    for m in (10_000, 10_000, 5_000):
        z = rng.standard_normal(m)
        x = disc * np.maximum(P["S0"] * np.exp(drift + vol * z) - P["K"], 0.0)
        sums.append(float(np.sum(x)))
        sumsq.append(float(np.sum(x * x)))
    got = simulate_shard(P, n, M, 2, chunk_size=chunk)
    assert to_hex(got.sum) == to_hex(math.fsum(sums)) and to_hex(got.sum_sq) == to_hex(
        math.fsum(sumsq)
    )


def test_within_3se_of_black_scholes():
    shards = []
    for i in range(4):
        r = simulate_shard(P, 50_000, M, i)
        shards.append(
            {"shard_index": i, "n": r.n, "sum_hex": to_hex(r.sum), "sum_sq_hex": to_hex(r.sum_sq)}
        )
    res = aggregate(shards)
    assert reference(P, res.estimate, res.std_error)["within_3se"]


def test_antithetic_matches_spec_and_counts_pairs():
    """§7.3 antithetic, recomputed literally: per chunk of m pairs, z and -z."""
    pairs, chunk = 25_000, 10_000
    rng = shard_generator(M, 3)
    drift = (P["r"] - 0.5 * P["sigma"] * P["sigma"]) * P["T"]
    vol = P["sigma"] * math.sqrt(P["T"])
    disc = math.exp(-P["r"] * P["T"])
    sums, sumsq = [], []
    for m in (10_000, 10_000, 5_000):
        z = rng.standard_normal(m)
        xp = disc * np.maximum(P["S0"] * np.exp(drift + vol * z) - P["K"], 0.0)
        xm = disc * np.maximum(P["S0"] * np.exp(drift + vol * -z) - P["K"], 0.0)
        x = 0.5 * (xp + xm)
        sums.append(float(np.sum(x)))
        sumsq.append(float(np.sum(x * x)))
    got = simulate_shard(P, 2 * pairs, M, 3, chunk_size=chunk, variance_reduction="antithetic")
    assert got.n == pairs
    assert to_hex(got.sum) == to_hex(math.fsum(sums)) and to_hex(got.sum_sq) == to_hex(
        math.fsum(sumsq)
    )
    again = simulate_shard(P, 2 * pairs, M, 3, chunk_size=chunk, variance_reduction="antithetic")
    assert to_hex(again.sum) == to_hex(got.sum)


def test_antithetic_reduces_variance_and_stays_within_3se():
    def run(vr, paths_per_shard):
        rows = []
        for i in range(4):
            r = simulate_shard(P, paths_per_shard, M, i, variance_reduction=vr)
            rows.append(
                {
                    "shard_index": i,
                    "n": r.n,
                    "sum_hex": to_hex(r.sum),
                    "sum_sq_hex": to_hex(r.sum_sq),
                }
            )
        return aggregate(rows)

    plain, anti = run("none", 50_000), run("antithetic", 50_000)
    assert anti.n == plain.n // 2
    assert anti.std_error < plain.std_error
    assert reference(P, anti.estimate, anti.std_error)["within_3se"]


def test_unknown_variance_reduction():
    with pytest.raises(ValueError):
        simulate_shard(P, 10, M, 0, variance_reduction="sobol")


def test_fingerprint_fields(monkeypatch):
    monkeypatch.delenv(fpm.ENFORCE_ENV, raising=False)
    fp = fpm.fingerprint()
    assert set(fpm.MATCH_KEYS) | {"cpu_model"} == set(fp)
    assert (
        fp["numpy"] == np.__version__
        and fp["bitgen"] == "PCG64DXSM"
        and fp["chunk_size"] == CHUNK_SIZE
    )
    assert fp["dispatch_enforced"] is False and fp["algo_version"] == "qmc-sim/v1"
    assert not any(isinstance(v, float) for v in fp.values())


def test_fingerprint_reads_proc_cpuinfo(tmp_path, monkeypatch):
    info = tmp_path / "cpuinfo"
    info.write_text(
        "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) X\nflags\t\t: fpu fma avx2\n"
        "processor\t: 1\nmodel name\t: other\nflags\t\t: avx512f\n"
    )
    monkeypatch.setattr(fpm, "CPUINFO", str(info))
    fp = fpm.fingerprint()
    assert (fp["fma"], fp["avx2"], fp["avx512f"], fp["cpu_model"]) == (
        True,
        True,
        False,
        "Intel(R) Xeon(R) X",
    )
    monkeypatch.setattr(fpm, "CPUINFO", str(tmp_path / "missing"))
    assert fpm.fingerprint()["fma"] is None


def test_exp_target_is_a_string():
    assert isinstance(fpm.exp_target(), str) and fpm.exp_target()


@pytest.mark.parametrize("target", ["AVX512_SKX", "AVX512_ICL", "X86_V4", "AVX512F"])
def test_self_check_raises_when_enforced(monkeypatch, target):
    monkeypatch.setattr(fpm, "exp_target", lambda: target)
    monkeypatch.setenv(fpm.ENFORCE_ENV, "true")
    with pytest.raises(fpm.DispatchError, match=target):
        fpm.assert_reproducible_dispatch()


def test_self_check_warns_when_not_enforced(monkeypatch, capsys):
    import json

    monkeypatch.setattr(fpm, "exp_target", lambda: "AVX512_SKX")
    monkeypatch.setenv(fpm.ENFORCE_ENV, "false")
    assert fpm.assert_reproducible_dispatch() == "AVX512_SKX"
    line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert line["event"] == "dispatch_not_pinned" and line["level"] == "WARNING"
    assert fpm.fingerprint()["dispatch_enforced"] is False


@pytest.mark.parametrize("target", ["AVX2", "X86_V3", "baseline(SSE SSE2 SSE3)", "FMA3__AVX2"])
def test_self_check_passes_non_avx512(monkeypatch, target):
    monkeypatch.setattr(fpm, "exp_target", lambda: target)
    monkeypatch.setenv(fpm.ENFORCE_ENV, "true")
    assert fpm.assert_reproducible_dispatch() == target
