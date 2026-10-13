"""QMC-108 antithetic variates (HANDOFF §7.3, §5.2): closed form and a golden shard.

The seed labels below were committed before any antithetic implementation existed (see git
history), so they cannot have been chosen to pass. With a fixed seed the 3 SE test is
deterministic and fails for about 0.27% of seeds; if it fails, report it to James. Never
change a label.
"""

from __future__ import annotations

import pytest

from qmc_core.aggregate import aggregate
from qmc_core.bs import reference
from qmc_core.params import split_paths

from ._shards import BLACK_SCHOLES, REFERENCE, frozen_seed, shard_item

SEED = frozen_seed("qmc-antithetic-closed-form-test-v1")
GOLDEN_SEED = frozen_seed("qmc-golden-antithetic-test-v1")
PATHS, SHARDS = 1_000_000, 10


def test_antithetic_split_simulates_pairs():
    split = split_paths(PATHS, SHARDS, "antithetic")
    assert sum(split) == PATHS and all(n % 2 == 0 for n in split)


def test_antithetic_one_million_paths_within_3se():
    split = split_paths(PATHS, SHARDS, "antithetic")
    result = aggregate(shard_item(SEED, i, n, "antithetic") for i, n in enumerate(split))
    z = (result.estimate - BLACK_SCHOLES) / result.std_error
    print(
        f"antithetic estimate={result.estimate!r} se={result.std_error!r} z={z:+.4f} pairs={result.n}"
    )
    assert result.n == PATHS // 2  # n counts pairs; SE is over pair means
    assert abs(z) < 3, f"z={z:+.4f}: report to James, do not change the seed"
    assert reference(REFERENCE, result.estimate, result.std_error)["within_3se"] is True


# Generated 2026-10-13 with `make test-golden` (Lambda image, amd64 under Rosetta, exp → X86_V3,
# NumPy 2.5.4); native arm64 gives the same bits. To be confirmed by CI's golden job on native x86
# and by the deployed antithetic job's shard cross-check. Never regenerate without James.
GOLDEN_ANTITHETIC_SUM_HEX: str | None = "0x1.fdeed45885d08p+18"
GOLDEN_ANTITHETIC_SUM_SQ_HEX: str | None = "0x1.efe11fda40dc7p+22"


@pytest.mark.lambda_image
def test_golden_antithetic_shard():
    from qmc_core.codec import to_hex
    from qmc_core.sim import simulate_shard

    r = simulate_shard(REFERENCE, 100_000, GOLDEN_SEED, 0, variance_reduction="antithetic")
    got = (to_hex(r.sum), to_hex(r.sum_sq))
    print(f'GOLDEN_ANTITHETIC_SUM_HEX = "{got[0]}"\nGOLDEN_ANTITHETIC_SUM_SQ_HEX = "{got[1]}"')
    assert r.n == 50_000
    assert GOLDEN_ANTITHETIC_SUM_HEX is not None, (
        f"no golden constants committed yet; generated: {got}"
    )
    assert got == (GOLDEN_ANTITHETIC_SUM_HEX, GOLDEN_ANTITHETIC_SUM_SQ_HEX)
