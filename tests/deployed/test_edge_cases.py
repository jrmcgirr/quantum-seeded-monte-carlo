"""Edge cases on the deployed stack (QMC-143). ``uv run pytest -m deployed tests/deployed -s``.

Needs AWS credentials that may invoke the Function URL (URL from ``$QMC_API_URL`` or terraform).
The validation and routing checks are rejected before the limiter: they spend no Qrypt.

Fail-closed drill (only during the drill): WS4 creates the decoy SecureString
``/qmc/qrypt-api-key-invalid``; the integrator applies with
``EXTRA_VARS='-var qrypt_api_key_param_name=/qmc/qrypt-api-key-invalid'``; then run with
``QMC_DRILL=fail-closed``. Qrypt answers a bad token with the same 403 as an exhausted quota,
so the expected code is QRYPT_FORBIDDEN (contract change 003). The integrator re-applies normally afterwards (plan = 0), and WS4
deletes the decoy.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.deployed


def request(method, path, body=None):
    from clients.qmc import request as signed  # lazy: collection must not need WS2's client

    return signed(method, path, body)


GOOD = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0, "paths": 1000, "shards": 1}


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        ({"sigma": 0}, {"sigma"}),
        ({"sigma": -0.2}, {"sigma"}),
        ({"T": 0}, {"T"}),
        ({"T": -1}, {"T"}),
        ({"paths": 0}, {"paths"}),
        ({"paths": -5}, {"paths"}),
        ({"paths": 10, "shards": 11}, {"shards"}),
        ({"S0": "100"}, {"S0"}),
        ({"K": True}, {"K"}),
        ({"paths": 1e6}, {"paths"}),
        ({"paths": 10_000_001}, {"paths"}),  # cost runaway: MAX_PATHS
        ({"paths": 2_000_000, "shards": 1}, {"paths"}),  # timeout bound: paths per shard
        ({"sigma": 0, "T": -1, "extra": 1}, {"sigma", "T", "extra"}),
    ],
)
def test_invalid_inputs_are_400_with_clear_details(change, fields):
    status, _, body = request("POST", "/jobs", {**GOOD, **change})
    err = body["error"]
    print(change, "→", status, err)
    assert status == 400 and err["code"] == "VALIDATION_ERROR"
    reported = {d["field"] for d in err["details"]}
    # The paths-per-shard ratio may be reported on paths or shards; the field must be named.
    assert fields <= reported or (fields == {"paths"} and reported & {"paths", "shards"})
    assert all(d["problem"] for d in err["details"])


def test_not_json_and_not_object():
    assert request("POST", "/jobs", [1, 2])[2]["error"]["code"] == "INVALID_JSON"


def test_unknown_job_404_names_id():
    job_id = "f" * 32
    status, _, body = request("GET", f"/jobs/{job_id}")
    assert status == 404 and body["error"]["code"] == "JOB_NOT_FOUND"
    assert job_id in body["error"]["message"]


def test_malformed_id_is_404_not_500():
    assert request("GET", "/jobs/not-an-id")[0] == 404


def test_unknown_route_and_wrong_method():
    assert request("GET", "/nope")[2]["error"]["code"] == "ROUTE_NOT_FOUND"
    assert request("DELETE", "/jobs")[0] == 405


@pytest.mark.skipif(os.environ.get("QMC_DRILL") != "fail-closed", reason="fail-closed drill only")
def test_fail_closed_bad_token():
    before = {j["job_id"] for j in request("GET", "/jobs")[2]["jobs"]}
    t0 = time.time()
    status, _, body = request("POST", "/jobs", GOOD)
    print(
        f"submit with the decoy token → {status} {body} at {time.strftime('%TZ', time.gmtime(t0))}"
    )
    assert status == 503 and body["error"]["code"] == "QRYPT_FORBIDDEN"  # contract change 003
    after = {j["job_id"] for j in request("GET", "/jobs")[2]["jobs"]}
    assert after == before, "a rejected job must not be listed"
    # The SeedingFailures alarm (300 s period) is polled by hand:
    # aws cloudwatch describe-alarms --alarm-names qmc-dev-seeding-failures
