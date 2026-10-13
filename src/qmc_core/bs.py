"""Closed-form Black-Scholes reference (HANDOFF §7.3, D30)."""

from __future__ import annotations

import math
from collections.abc import Mapping


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_call(S0: float, K: float, r: float, sigma: float, T: float) -> float:
    """European call; ``N(x) = 0.5 * (1 + erf(x / sqrt(2)))``. Reference case → 10.450583572185565."""
    sd = sigma * math.sqrt(T)
    d1 = (math.log(S0 / K) + (r + 0.5 * sigma * sigma) * T) / sd
    d2 = d1 - sd
    return S0 * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def reference(params: Mapping[str, float], estimate: float, std_error: float | None) -> dict:
    """``{"black_scholes", "z_score", "within_3se"}``; z and within_3se are None without an SE."""
    bs = black_scholes_call(*(float(params[k]) for k in ("S0", "K", "r", "sigma", "T")))
    if not std_error:
        return {"black_scholes": bs, "z_score": None, "within_3se": None}
    z = (estimate - bs) / std_error
    return {"black_scholes": bs, "z_score": z, "within_3se": abs(z) <= 3.0}
