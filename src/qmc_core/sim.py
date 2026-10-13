"""Shard simulation, version ``qmc-sim/v1`` (HANDOFF §7.3). Imports NumPy: worker only.

The operation order is the product: Python floats for drift/vol/disc, ``np.exp`` on the
array, ``np.maximum``, per-chunk ``np.sum``, ``math.fsum`` across chunks. No ``@``,
``np.dot``, ``np.mean`` or BLAS.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from qmc_core.aggregate import ShardResult
from qmc_core.params import ALGO_VERSION, CHUNK_SIZE
from qmc_core.seeds import shard_generator

__all__ = ["ALGO_VERSION", "CHUNK_SIZE", "ShardResult", "simulate_shard"]


def simulate_shard(
    params: Mapping[str, float],
    shard_paths: int,
    master_int: int,
    shard_index: int,
    chunk_size: int = CHUNK_SIZE,
    variance_reduction: str = "none",
) -> ShardResult:
    """Simulate one shard; ``params`` has float ``S0, K, r, sigma, T``.

    ``"antithetic"`` simulates ``shard_paths // 2`` pairs and returns ``n`` = pairs (§7.3).
    """
    if variance_reduction not in ("none", "antithetic"):
        raise ValueError(f"unknown variance_reduction {variance_reduction!r}")
    S0, K, r, sigma, T = (float(params[k]) for k in ("S0", "K", "r", "sigma", "T"))
    drift = (r - 0.5 * sigma * sigma) * T
    vol = sigma * math.sqrt(T)
    disc = math.exp(-r * T)
    rng = shard_generator(master_int, shard_index)
    sums: list[float] = []
    sumsq: list[float] = []
    if variance_reduction == "antithetic":
        # §7.3: per chunk of m pairs, z and -z; the sample unit (n) is the pair.
        remaining = pairs = shard_paths // 2
        while remaining > 0:
            m = min(chunk_size, remaining)
            z = rng.standard_normal(m)
            z_minus = -z
            x_plus = disc * np.maximum(S0 * np.exp(drift + vol * z) - K, 0.0)
            x_minus = disc * np.maximum(S0 * np.exp(drift + vol * z_minus) - K, 0.0)
            x = 0.5 * (x_plus + x_minus)
            sums.append(float(np.sum(x)))
            sumsq.append(float(np.sum(x * x)))
            remaining -= m
        return ShardResult(n=pairs, sum=math.fsum(sums), sum_sq=math.fsum(sumsq))
    remaining = shard_paths
    while remaining > 0:
        m = min(chunk_size, remaining)
        z = rng.standard_normal(m)
        s_t = S0 * np.exp(drift + vol * z)
        x = disc * np.maximum(s_t - K, 0.0)
        sums.append(float(np.sum(x)))
        sumsq.append(float(np.sum(x * x)))
        remaining -= m
    return ShardResult(n=shard_paths, sum=math.fsum(sums), sum_sq=math.fsum(sumsq))
