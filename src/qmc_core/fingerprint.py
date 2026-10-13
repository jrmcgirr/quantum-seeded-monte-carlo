"""Runtime fingerprint and the fail-closed SIMD self-check (HANDOFF §7.9). Imports NumPy: worker only."""

from __future__ import annotations

ENFORCE_ENV = "QMC_ENFORCE_PINNED_DISPATCH"
# Fields compared by replay; cpu_model is informational and excluded.
MATCH_KEYS = (
    "python",
    "numpy",
    "machine",
    "libc",
    "exp_target",
    "algo_version",
    "bitgen",
    "chunk_size",
    "dispatch_enforced",
    "fma",
    "avx2",
    "avx512f",
)


class DispatchError(RuntimeError):
    """NumPy dispatched float64 ``exp`` to an AVX-512-family target under enforcement."""


def exp_target() -> str:
    """The SIMD target ``np.exp`` (float64) dispatches to, from ``numpy.lib.introspect.opt_func_info``."""
    raise NotImplementedError


def assert_reproducible_dispatch() -> str:
    """Return the exp target. If it is AVX-512-family (``AVX512*``, ``X86_V4``): raise
    ``DispatchError`` when ``QMC_ENFORCE_PINNED_DISPATCH=true``, else log a warning."""
    raise NotImplementedError


def fingerprint() -> dict:
    """``python, numpy, machine, libc, exp_target, algo_version, bitgen, chunk_size,
    dispatch_enforced, fma, avx2, avx512f, cpu_model`` (strings, ints, bools; no floats)."""
    raise NotImplementedError
