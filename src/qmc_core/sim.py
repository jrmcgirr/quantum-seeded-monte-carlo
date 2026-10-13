"""Shard simulation, version ``qmc-sim/v1`` (HANDOFF §7.3). Imports NumPy: worker only.

The operation order is the product: Python floats for drift/vol/disc, ``np.exp`` on the
array, ``np.maximum``, per-chunk ``np.sum``, ``math.fsum`` across chunks. No ``@``,
``np.dot``, ``np.mean`` or BLAS.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np  # noqa: F401  (the only NumPy importers are sim and fingerprint)

from qmc_core.aggregate import ShardResult

ALGO_VERSION = "qmc-sim/v1"
CHUNK_SIZE = 131_072  # 2**17; never change for an existing ALGO_VERSION


def simulate_shard(
    params: Mapping[str, float],
    shard_paths: int,
    master_int: int,
    shard_index: int,
    chunk_size: int = CHUNK_SIZE,
    variance_reduction: str = "none",
) -> ShardResult:
    """Simulate one shard; ``params`` has float ``S0, K, r, sigma, T``.

    ``"antithetic"`` raises NotImplementedError until QMC-108.
    """
    raise NotImplementedError
