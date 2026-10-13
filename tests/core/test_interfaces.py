"""G0a smoke test: every module imports, and the API-side modules never load NumPy."""

import importlib
import subprocess
import sys


def test_modules_import():
    for name in (
        "params",
        "seeds",
        "codec",
        "aggregate",
        "replay",
        "ddb",
        "messages",
        "errors",
        "emf",
        "logjson",
        "cost",
        "bs",
        "sim",
        "fingerprint",
    ):
        importlib.import_module(f"qmc_core.{name}")


def test_api_modules_do_not_import_numpy():
    code = (
        "import sys\n"
        "import qmc_core, qmc_core.params, qmc_core.seeds, qmc_core.codec, qmc_core.aggregate, "
        "qmc_core.replay, qmc_core.ddb, qmc_core.messages, qmc_core.errors, qmc_core.emf, "
        "qmc_core.logjson, qmc_core.cost, qmc_core.bs, qmc_api.handler\n"
        "assert 'numpy' not in sys.modules, 'numpy imported on the API path'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env={"PYTHONPATH": "src"})

