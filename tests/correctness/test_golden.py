"""Core 5 (I5): one fixed-seed shard is bit-identical to committed constants on the Lambda image.

Runs only in public.ecr.aws/lambda/python:3.13 (``make test-golden`` / CI ``golden`` job), never
on a laptop: macOS arm64 uses a different ``exp``. The constants were generated there once;
never regenerate them without James.
"""

from __future__ import annotations

import pytest

from ._shards import REFERENCE, frozen_seed

pytestmark = pytest.mark.lambda_image

SEED = frozen_seed("qmc-golden-test-v1")
SHARD_INDEX = 0
SHARD_PATHS = 100_000

# Generated 2026-10-13 with `make test-golden` (Lambda image, amd64 under Rosetta, exp → X86_V3,
# NumPy 2.5.4). Confirmed on native x86_64 (GitHub Actions run 38392842904, ubuntu-24.04) and
# against real Lambda shards (tools/shard_crosscheck.py). Never regenerate without James.
GOLDEN_SUM_HEX: str | None = "0x1.fdcf3646e51c2p+19"
GOLDEN_SUM_SQ_HEX: str | None = "0x1.f2c02fc1b1bcep+24"


def test_exp_target_is_not_avx512():
    from qmc_core.fingerprint import assert_reproducible_dispatch

    target = assert_reproducible_dispatch()  # raises under QMC_ENFORCE_PINNED_DISPATCH=true
    print(f"exp target: {target}")
    assert not target.upper().startswith(("AVX512", "X86_V4"))


def test_golden_shard():
    from qmc_core.codec import to_hex
    from qmc_core.sim import simulate_shard

    r = simulate_shard(REFERENCE, SHARD_PATHS, SEED, SHARD_INDEX)
    got = (to_hex(r.sum), to_hex(r.sum_sq))
    print(f'GOLDEN_SUM_HEX = "{got[0]}"\nGOLDEN_SUM_SQ_HEX = "{got[1]}"')
    assert r.n == SHARD_PATHS
    assert GOLDEN_SUM_HEX is not None, f"no golden constants committed yet; generated: {got}"
    assert got == (GOLDEN_SUM_HEX, GOLDEN_SUM_SQ_HEX)
