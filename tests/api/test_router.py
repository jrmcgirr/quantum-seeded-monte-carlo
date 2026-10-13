"""QMC-120: routing, error mapping, GET /jobs, GET /jobs/{id} not-found, /health."""

from __future__ import annotations

import pytest

from tests.api.conftest import REF


def test_fresh_table_lists_no_jobs(api):
    status, _, body = api.call("GET", "/jobs")
    assert (status, body) == (200, {"jobs": []})


def test_list_newest_first_with_current_status(api):
    ids = [api.call("POST", "/jobs", REF)[2]["job_id"] for _ in range(3)]
    api.table.update_item(
        Key={"pk": f"JOB#{ids[0]}", "sk": "META"},
        UpdateExpression="SET #s = :c",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "COMPLETE"},
    )
    status, _, body = api.call("GET", "/jobs")
    assert status == 200
    assert [j["job_id"] for j in body["jobs"]] == ids[::-1]
    assert body["jobs"][-1]["status"] == "COMPLETE"
    assert body["jobs"][0]["status"] == "QUEUED"
    assert set(body["jobs"][0]) == {
        "job_id",
        "status",
        "created_at",
        "paths",
        "shards",
        "replay_of",
    }


def test_list_never_scans(api, iam_guard):
    from qmc_api import handler
    from tests.api.conftest import Ctx, event

    with iam_guard("api") as calls:
        handler.handle(event("GET", "/jobs"), Ctx())
    assert ("dynamodb", "Scan") not in calls
    assert ("dynamodb", "Query") in calls


@pytest.mark.parametrize("job_id", ["0" * 32, "ABCDEF" + "0" * 26, "x", "0" * 33, "../etc"])
def test_unknown_or_malformed_id_is_404_naming_it(api, job_id):
    status, _, body = api.call("GET", f"/jobs/{job_id}")
    assert status == 404
    assert body["error"]["code"] == "JOB_NOT_FOUND"
    assert job_id in body["error"]["message"]


def test_unknown_route_and_wrong_method(api):
    assert api.call("GET", "/nope")[2]["error"]["code"] == "ROUTE_NOT_FOUND"
    status, _, body = api.call("DELETE", "/jobs")
    assert (status, body["error"]["code"]) == (405, "METHOD_NOT_ALLOWED")
    assert api.call("POST", "/health")[0] == 405
    assert api.call("GET", "/jobs/" + "0" * 32 + "/replay")[0] == 405


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", "NaN"])
def test_bad_json_is_400_invalid_json(api, raw):
    status, _, body = api.call("POST", "/jobs", raw=raw)
    assert (status, body["error"]["code"]) == (400, "INVALID_JSON")


def test_base64_body_is_decoded(api):
    status, _, body = api.call("POST", "/jobs", REF, b64=True)
    assert status == 202, body


def test_validation_error_lists_every_field(api):
    status, _, body = api.call("POST", "/jobs", {**REF, "S0": -1, "paths": 1e6, "extra": 1})
    assert (status, body["error"]["code"]) == (400, "VALIDATION_ERROR")
    fields = {d["field"] for d in body["error"]["details"]}
    assert {"S0", "paths", "extra"} <= fields


def test_handler_exception_is_500_with_request_id(api, monkeypatch):
    from qmc_api import handler

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(handler.ROUTES[0][1], "GET", boom)
    status, _, body = api.call("GET", "/jobs")
    assert (status, body["error"]["code"]) == (500, "INTERNAL_ERROR")
    assert "req-test-0001" in body["error"]["message"]
    assert "kaboom" not in str(body) and "Traceback" not in str(body)


def test_malformed_event_is_500_not_a_raise(api):
    from qmc_api import handler
    from tests.api.conftest import Ctx

    resp = handler.handle({}, Ctx())
    assert resp["statusCode"] == 500


def test_health_ok(api):
    status, _, body = api.call("GET", "/health")
    assert status == 200
    assert body == {
        "status": "ok",
        "checks": {"dynamodb": "ok", "ssm": "ok"},
        "build_sha": "test",
        "env": "test",
    }


def test_health_503_names_failing_check(api, monkeypatch):
    monkeypatch.setenv("QRYPT_API_KEY_PARAM", "/qmc/missing")
    status, _, body = api.call("GET", "/health")
    assert status == 503
    assert body["checks"]["dynamodb"] == "ok"
    assert body["checks"]["ssm"].startswith("error")


def test_request_headers_never_logged(api, capsys):
    api.call("GET", "/jobs")
    assert "SECRET-HEADER" not in capsys.readouterr().out
