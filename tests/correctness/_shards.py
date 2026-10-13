"""Shared helpers for the correctness tests: frozen seeds and the reference case."""

from __future__ import annotations

import hashlib

from qmc_core.codec import to_hex
from qmc_core.sim import simulate_shard

REFERENCE = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}
BLACK_SCHOLES = 10.450583572185565


def frozen_seed(label: str) -> int:
    """A master seed frozen by name. Never change a label to get a passing seed."""
    return int.from_bytes(hashlib.sha256(label.encode("ascii")).digest(), "big")


def shard_item(master_int: int, i: int, shard_paths: int) -> dict:
    r = simulate_shard(REFERENCE, shard_paths, master_int, i)
    return {"shard_index": i, "n": r.n, "sum_hex": to_hex(r.sum), "sum_sq_hex": to_hex(r.sum_sq)}
