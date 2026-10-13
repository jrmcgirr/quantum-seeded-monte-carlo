"""Runtime fingerprint and the fail-closed SIMD self-check (HANDOFF §7.9). Imports NumPy: worker only."""

from __future__ import annotations

import os
import platform

import numpy as np
from numpy.lib.introspect import opt_func_info

from qmc_core import logjson
from qmc_core.params import ALGO_VERSION, CHUNK_SIZE
from qmc_core.seeds import BITGEN

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
CPUINFO = "/proc/cpuinfo"


class DispatchError(RuntimeError):
    """NumPy dispatched float64 ``exp`` to an AVX-512-family target under enforcement."""


def exp_target() -> str:
    """The SIMD target ``np.exp`` (float64) dispatches to, from ``numpy.lib.introspect.opt_func_info``."""
    info = opt_func_info(func_name="^exp$", signature="float64")
    (entry,) = info["exp"].values()
    return entry["current"]


def is_avx512_family(target: str) -> bool:
    return "AVX512" in target or "X86_V4" in target


def enforced() -> bool:
    return os.environ.get(ENFORCE_ENV, "").lower() == "true"


def assert_reproducible_dispatch() -> str:
    """Return the exp target. If it is AVX-512-family (``AVX512*``, ``X86_V4``): raise
    ``DispatchError`` when ``QMC_ENFORCE_PINNED_DISPATCH=true``, else log a warning."""
    target = exp_target()
    if is_avx512_family(target):
        msg = (
            f"np.exp float64 dispatches to {target}; NPY_DISABLE_CPU_FEATURES="
            f"{os.environ.get('NPY_DISABLE_CPU_FEATURES', '')!r} did not pin it"
        )
        if enforced():
            raise DispatchError(msg)
        logjson.log("dispatch_not_pinned", level="WARNING", exp_target=target, detail=msg)
    return target


def _cpuinfo() -> tuple[set[str] | None, str | None]:
    try:
        with open(CPUINFO) as f:
            text = f.read()
    except OSError:
        return None, None
    flags: set[str] = set()
    model = None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "flags" and not flags:
            flags = set(value.split())
        elif key == "model name" and model is None:
            model = value.strip()
    return flags, model


def fingerprint() -> dict:
    """``python, numpy, machine, libc, exp_target, algo_version, bitgen, chunk_size,
    dispatch_enforced, fma, avx2, avx512f, cpu_model`` (strings, ints, bools; no floats)."""
    flags, model = _cpuinfo()
    lib, version = platform.libc_ver()
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "machine": platform.machine(),
        "libc": f"{lib} {version}".strip(),
        "exp_target": exp_target(),
        "algo_version": ALGO_VERSION,
        "bitgen": BITGEN,
        "chunk_size": CHUNK_SIZE,
        "dispatch_enforced": enforced(),
        "fma": None if flags is None else "fma" in flags,
        "avx2": None if flags is None else "avx2" in flags,
        "avx512f": None if flags is None else "avx512f" in flags,
        "cpu_model": model,
    }
