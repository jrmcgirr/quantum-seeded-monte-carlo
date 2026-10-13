"""Core 2: shard seeds are distinct, reproducible and uncorrelated (HANDOFF §11, QMC-140)."""

from __future__ import annotations

import numpy as np

from qmc_core.seeds import shard_generator

from ._shards import frozen_seed

SEED = frozen_seed("qmc-seed-independence-test-v1")


def _state(gen) -> int:
    return gen.bit_generator.state["state"]["state"]


def test_1000_shard_seeds_are_distinct():
    states = {_state(shard_generator(SEED, i)) for i in range(1000)}
    assert len(states) == 1000


def test_same_master_same_streams():
    for i in (0, 1, 999):
        a = shard_generator(SEED, i).standard_normal(1000)
        b = shard_generator(SEED, i).standard_normal(1000)
        assert a.tobytes() == b.tobytes()


def test_different_master_different_streams():
    a = shard_generator(SEED, 0).standard_normal(1000)
    b = shard_generator(SEED + 1, 0).standard_normal(1000)
    assert a.tobytes() != b.tobytes()


def test_spawn_key_equals_spawn():
    children = np.random.SeedSequence(SEED).spawn(5)
    for i, child in enumerate(children):
        expected = np.random.Generator(np.random.PCG64DXSM(child)).standard_normal(100)
        assert shard_generator(SEED, i).standard_normal(100).tobytes() == expected.tobytes()


def test_20_streams_uncorrelated():
    n = 10_000
    draws = np.stack([shard_generator(SEED, i).standard_normal(n) for i in range(20)])
    corr = np.corrcoef(draws)
    off_diag = np.abs(corr[~np.eye(20, dtype=bool)])
    print(f"max |corr| = {off_diag.max():.5f} (bound {5 / np.sqrt(n):.5f})")
    assert off_diag.max() < 5 / np.sqrt(n)
