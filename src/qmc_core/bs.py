"""Closed-form Black-Scholes reference (HANDOFF §7.3, D30)."""

from __future__ import annotations

from collections.abc import Mapping


def black_scholes_call(S0: float, K: float, r: float, sigma: float, T: float) -> float:
    """European call; ``N(x) = 0.5 * (1 + erf(x / sqrt(2)))``. Reference case → 10.450583572185565."""
    raise NotImplementedError


def reference(params: Mapping[str, float], estimate: float, std_error: float | None) -> dict:
    """``{"black_scholes", "z_score", "within_3se"}``; z and within_3se are None without an SE."""
    raise NotImplementedError
