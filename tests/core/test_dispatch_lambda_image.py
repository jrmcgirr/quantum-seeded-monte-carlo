"""Runs only in public.ecr.aws/lambda/python:3.13 (CI golden job, make test-golden): QMC-106.

There the enforcement flag is on and NPY_DISABLE_CPU_FEATURES is set, so the float64 exp target
must not be AVX-512, and the self-check must pass.
"""

import os
import platform

import pytest

pytestmark = pytest.mark.lambda_image


def test_runtime_is_the_lambda_image():
    assert platform.machine() == "x86_64"
    assert platform.python_version().startswith("3.13.")
    assert os.environ.get("QMC_ENFORCE_PINNED_DISPATCH") == "true"
    assert os.environ.get("NPY_DISABLE_CPU_FEATURES")


def test_exp_target_is_not_avx512_and_self_check_passes():
    from qmc_core import fingerprint as fpm

    target = fpm.assert_reproducible_dispatch()
    assert not fpm.is_avx512_family(target), target
    fp = fpm.fingerprint()
    assert fp["dispatch_enforced"] is True and fp["exp_target"] == target
    print("fingerprint:", fp)


def test_numpy_is_the_pinned_version():
    import pathlib
    import re

    import numpy as np

    req = pathlib.Path(__file__).resolve().parents[2] / "requirements" / "worker.txt"
    assert re.search(r"numpy==([\d.]+)", req.read_text()).group(1) == np.__version__


def test_every_disabled_name_is_a_real_dispatch_group():
    """NumPy 2.5 silently ignores unknown names in NPY_DISABLE_CPU_FEATURES (QMC-099 finding)."""
    import numpy as np

    simd = np.__config__.CONFIG["SIMD Extensions"]
    known = set(simd["baseline"]) | set(simd["found"]) | set(simd["not found"])
    names = os.environ["NPY_DISABLE_CPU_FEATURES"].split()
    assert names and set(names) <= known, (names, known)


def test_sim_ufuncs_dispatch_to_x86_v3():
    """Every float64 ufunc in the §7.3 path runs the same X86_V3 kernels on every Lambda host."""
    from numpy.lib.introspect import opt_func_info

    for name in ("exp", "add", "multiply", "subtract", "maximum"):
        (entry,) = opt_func_info(func_name=f"^{name}$", signature="float64")[name].values()
        assert entry["current"] == "X86_V3", (name, entry)
