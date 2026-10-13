"""Core 4: a 1M-path estimate lands within 3 SE of Black-Scholes (HANDOFF §11, QMC-140).

The seed is frozen. With a fixed seed this test is deterministic and fails for about 0.27% of
seeds; if it fails, report it to James. Never pick another seed.
"""

from __future__ import annotations

from qmc_core.aggregate import aggregate
from qmc_core.bs import black_scholes_call, reference
from qmc_core.params import split_paths

from ._shards import BLACK_SCHOLES, REFERENCE, frozen_seed, shard_item

SEED = frozen_seed("qmc-closed-form-test-v1")


def test_black_scholes_reference_value():
    assert abs(black_scholes_call(**REFERENCE) - BLACK_SCHOLES) <= 1e-12


def test_one_million_paths_within_3se():
    split = split_paths(1_000_000, 10)
    result = aggregate(shard_item(SEED, i, n) for i, n in enumerate(split))
    ref = reference(REFERENCE, result.estimate, result.std_error)
    z = (result.estimate - BLACK_SCHOLES) / result.std_error
    print(f"estimate={result.estimate!r} se={result.std_error!r} z={z:+.4f}")
    assert result.n == 1_000_000
    assert abs(z) < 3, f"z={z:+.4f}: report to James, do not change the seed"
    assert ref["within_3se"] is True
    assert abs(ref["z_score"] - z) < 1e-12
