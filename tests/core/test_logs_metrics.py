import json
from decimal import Decimal

import pytest

from qmc_core import emf, logjson
from qmc_core.errors import EntropyError, ErrorCode, QmcError


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setenv("QMC_ENV", "test")
    monkeypatch.delenv("METRICS_NAMESPACE", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    logjson.configure("worker")
    logjson.clear()
    yield
    logjson.clear()


def lines(capsys):
    out = capsys.readouterr().out
    parsed = [json.loads(line) for line in out.splitlines()]
    assert all(isinstance(p, dict) for p in parsed) and out.endswith("\n")
    return parsed


def test_emf_shape(capsys, monkeypatch):
    monkeypatch.setenv("METRICS_NAMESPACE", "QMC/dev")
    emf.emit("JobsSubmitted", 1, job_id="abc", replay_of=None)
    (obj,) = lines(capsys)
    assert obj["_aws"]["CloudWatchMetrics"] == [
        {
            "Namespace": "QMC/dev",
            "Dimensions": [[]],
            "Metrics": [{"Name": "JobsSubmitted", "Unit": "Count"}],
        }
    ]
    assert (
        isinstance(obj["_aws"]["Timestamp"], int)
        and obj["JobsSubmitted"] == 1
        and obj["job_id"] == "abc"
    )


def test_emf_every_metric_and_default_namespace(capsys):
    for name in emf.METRICS:
        emf.emit(name, 2.5 if name == "QryptLatencyMs" else 1)
    objs = lines(capsys)
    assert len(objs) == 8 == len(emf.METRICS)
    assert {o["_aws"]["CloudWatchMetrics"][0]["Namespace"] for o in objs} == {"QMC/test"}
    assert objs[1]["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Unit"] == "Kilobytes"


@pytest.mark.parametrize("args", [("Nope", 1), ("JobsSubmitted", True), ("JobsSubmitted", "1")])
def test_emf_rejects(args):
    with pytest.raises(ValueError):
        emf.emit(*args)
    with pytest.raises(ValueError):
        emf.emit("JobsSubmitted", 1, JobsSubmitted=2)


def test_emf_never_uses_logging():
    import inspect

    for mod in (emf, logjson):
        assert "import logging" not in inspect.getsource(mod)


def test_log_fields_context_and_redaction(capsys):
    logjson.bind(request_id="req-1", job_id="j")
    logjson.log(
        "shard_written",
        shard_index=3,
        n=Decimal(5),
        done={Decimal(2), Decimal(1)},
        headers={"Authorization": "Bearer x", "X-Api-Key": "k", "accept": "json"},
        nested=[{"qrypt_token": "t", "ok": 1}],
        raw_block="AAAA",
        api_key="k",
        note="Bearer abc.def",
        Secret="s",
        password="p",
    )
    (obj,) = lines(capsys)
    assert obj["event"] == "shard_written" and obj["level"] == "INFO" and obj["fn"] == "worker"
    assert obj["env"] == "test" and obj["request_id"] == "req-1" and obj["ts"].endswith("Z")
    assert obj["n"] == 5 and obj["done"] == [1, 2]
    assert obj["headers"] == {"accept": "json"} and obj["nested"] == [{"ok": 1}]
    assert obj["note"] == "[REDACTED]"
    for k in ("raw_block", "api_key", "Secret", "password"):
        assert k not in obj
    logjson.clear()
    logjson.log("x")
    assert "request_id" not in lines(capsys)[0]


def test_log_levels(capsys, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    assert logjson.log("quiet", level="info") is None
    assert logjson.log("loud", level="ERROR")["level"] == "ERROR"
    assert [o["event"] for o in lines(capsys)] == ["loud"]


def test_log_odd_values(capsys):
    logjson.log("x", ratio=Decimal("0.5"), obj=object(), t=("a", 1))
    obj = lines(capsys)[0]
    assert obj["ratio"] == "0.5" and obj["t"] == ["a", 1] and obj["obj"].startswith("<object")


def test_errors():
    e = QmcError(ErrorCode.VALIDATION_ERROR, details=[{"field": "S0", "problem": "required"}])
    assert e.http_status == 400 and e.message == ErrorCode.VALIDATION_ERROR.default_message
    assert e.to_body() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": e.message,
            "details": [{"field": "S0", "problem": "required"}],
        }
    }
    r = QmcError(ErrorCode.ENTROPY_RATE_LIMITED, "slow down", retry_after=7)
    assert r.to_body() == {"error": {"code": "ENTROPY_RATE_LIMITED", "message": "slow down"}}
    assert r.retry_after == 7 and r.http_status == 429
    assert isinstance(EntropyError(ErrorCode.QRYPT_TIMEOUT), QmcError)
    assert {c.http_status for c in ErrorCode} == {400, 404, 405, 409, 429, 500, 502, 503}
    assert len(ErrorCode) == 22


def test_cost_is_a_stub():
    from qmc_core.cost import estimate

    assert estimate({}, []) is None
