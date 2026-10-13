import os
import subprocess

import pytest


@pytest.fixture(autouse=True, scope="session")
def api_url():
    """``$QMC_API_URL``, else ``make outputs`` (initialises this checkout against the shared state)."""
    if not os.environ.get("QMC_API_URL"):
        import json

        out = subprocess.run(["make", "-s", "outputs"], check=True, capture_output=True, text=True)
        os.environ["QMC_API_URL"] = json.loads(out.stdout)["api_url"]["value"]
    return os.environ["QMC_API_URL"]
