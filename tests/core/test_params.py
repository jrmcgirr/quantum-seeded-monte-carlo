import base64
import json

import pytest

from qmc_core.errors import ErrorCode, QmcError
from qmc_core.params import Limits, is_job_id, parse_body, split_paths, validate_job_request

GOOD = {"S0": 100, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 1_000_000, "shards": 10}
L = Limits()


def errs(body, fi=False, limits=L):
    with pytest.raises(QmcError) as e:
        validate_job_request(body, limits, fi)
    return e.value


def fields(body, **kw):
    err = errs(body, **kw)
    assert err.code is ErrorCode.VALIDATION_ERROR
    return {(d["field"], d["problem"]) for d in err.details}


def test_valid_request_converts_ints_to_floats():
    spec = validate_job_request(GOOD, L, False)
    assert spec.params() == {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}
    assert isinstance(spec.S0, float) and spec.paths == 1_000_000 and spec.shards == 10
    assert spec.variance_reduction == "none" and spec.fail_shards == ()


@pytest.mark.parametrize(
    "name,value",
    [
        ("S0", 0),
        ("S0", -1),
        ("S0", 1e6 + 1),
        ("S0", True),
        ("S0", "100"),
        ("S0", None),
        ("K", 0.0),
        ("K", float("nan")),
        ("K", float("inf")),
        ("K", 10**400),
        ("r", -1.0000001),
        ("r", 1.5),
        ("r", False),
        ("sigma", 0),
        ("sigma", 5.01),
        ("sigma", [0.2]),
        ("T", 0),
        ("T", 50.5),
        ("T", {"x": 1}),
        ("paths", 1e6),
        ("paths", 1_000_000.0),
        ("paths", True),
        ("paths", 1),
        ("paths", 10_000_001),
        ("paths", "1000"),
        ("shards", 0),
        ("shards", 51),
        ("shards", 2.0),
        ("shards", False),
    ],
)
def test_each_rule(name, value):
    assert name in {f for f, _ in fields({**GOOD, name: value})}


def test_boundaries_accepted():
    for body in (
        {**GOOD, "S0": 1e6, "K": 1e6, "r": -1, "sigma": 5, "T": 50},
        {**GOOD, "r": 1, "paths": 2, "shards": 1},
        {**GOOD, "paths": 10_000_000, "shards": 10},
        {**GOOD, "S0": 5e-324},
    ):
        validate_job_request(body, L, False)


def test_missing_and_unknown_collected_together():
    got = fields({"S0": True, "extra": 1, "another": 2})
    assert ("S0", "must be a JSON number") in got
    for f in ("K", "r", "sigma", "T", "paths", "shards"):
        assert (f, "required") in got
    assert ("extra", "unknown field") in got and ("another", "unknown field") in got


def test_shards_greater_than_paths():
    assert ("shards", "must be <= paths") in fields({**GOOD, "paths": 5, "shards": 6})


def test_per_shard_ratio():
    assert {f for f, _ in fields({**GOOD, "paths": 2_000_001, "shards": 2})} == {"paths"}
    validate_job_request({**GOOD, "paths": 2_000_000, "shards": 2}, L, False)
    small = Limits(max_paths=100, max_shards=5, max_paths_per_shard=10)
    assert {f for f, _ in fields({**GOOD, "paths": 51, "shards": 5}, limits=small)} == {"paths"}


def test_variance_reduction():
    assert ("variance_reduction", 'must be "none" or "antithetic"') in fields(
        {**GOOD, "variance_reduction": "sobol"}
    )
    assert validate_job_request({**GOOD, "variance_reduction": "none"}, L, False)
    assert errs({**GOOD, "variance_reduction": "antithetic"}).code is ErrorCode.UNSUPPORTED


def test_validation_error_wins_over_unsupported():
    err = errs({**GOOD, "variance_reduction": "antithetic", "S0": -1})
    assert err.code is ErrorCode.VALIDATION_ERROR


def test_fault_injection_disabled():
    err = errs({**GOOD, "fault_injection": {"fail_shards": [3]}}, fi=False)
    assert err.code is ErrorCode.FAULT_INJECTION_DISABLED
    assert err.http_status == 400


def test_fault_injection_enabled():
    spec = validate_job_request({**GOOD, "fault_injection": {"fail_shards": [3, 1, 3]}}, L, True)
    assert spec.fail_shards == (1, 3)


@pytest.mark.parametrize(
    "fi",
    [
        [3],
        {"fail_shards": 3},
        {"fail_shards": [3], "x": 1},
        {},
        {"fail_shards": [10]},
        {"fail_shards": [-1]},
        {"fail_shards": [True]},
        {"fail_shards": ["1"]},
    ],
)
def test_fault_injection_malformed(fi):
    assert any(
        f.startswith("fault_injection") for f, _ in fields({**GOOD, "fault_injection": fi}, fi=True)
    )


def test_fault_injection_with_invalid_shards():
    got = fields({**GOOD, "shards": 0, "fault_injection": {"fail_shards": [0]}}, fi=True)
    assert {f for f, _ in got} == {"shards", "fault_injection.fail_shards"}


def test_split_paths():
    for paths, shards in [(1_000_000, 10), (7, 3), (2, 2), (10_000_000, 50), (101, 7)]:
        split = split_paths(paths, shards)
        assert sum(split) == paths and len(split) == shards
        assert max(split) - min(split) <= 1 and split == sorted(split, reverse=True)
    assert split_paths(7, 3) == [3, 2, 2]


def test_split_paths_antithetic():
    assert split_paths(10, 3, "antithetic") == [4, 4, 2]
    assert sum(split_paths(1_000_000, 10, "antithetic")) == 1_000_000


def test_limits_from_env(monkeypatch):
    assert Limits.from_env() == Limits()
    monkeypatch.setenv("MAX_PATHS", "100")
    monkeypatch.setenv("MAX_SHARDS", "4")
    monkeypatch.setenv("MAX_PATHS_PER_SHARD", "30")
    assert Limits.from_env() == Limits(100, 4, 30)


def test_parse_body():
    assert parse_body(json.dumps(GOOD)) == GOOD
    assert parse_body(json.dumps(GOOD).encode()) == GOOD
    assert parse_body(base64.b64encode(json.dumps(GOOD).encode()), is_base64=True) == GOOD


@pytest.mark.parametrize(
    "raw,b64,code,message",
    [
        (None, False, ErrorCode.INVALID_JSON, None),
        ("", False, ErrorCode.INVALID_JSON, None),
        ("{", False, ErrorCode.INVALID_JSON, None),
        ('{"S0": NaN}', False, ErrorCode.INVALID_JSON, None),
        ('{"S0": Infinity}', False, ErrorCode.INVALID_JSON, None),
        ("[1, 2]", False, ErrorCode.INVALID_JSON, "body must be a JSON object"),
        ("42", False, ErrorCode.INVALID_JSON, "body must be a JSON object"),
        (b"\xff\xfe", False, ErrorCode.INVALID_JSON, None),
        ("not base64!", True, ErrorCode.INVALID_JSON, "body is not valid base64"),
        ("{" + " " * 16 * 1024 + "}", False, ErrorCode.BODY_TOO_LARGE, None),
    ],
)
def test_parse_body_rejects(raw, b64, code, message):
    with pytest.raises(QmcError) as e:
        parse_body(raw, is_base64=b64)
    assert e.value.code is code
    if message:
        assert e.value.message == message


def test_is_job_id():
    assert is_job_id("0123456789abcdef0123456789abcdef")
    for bad in ("0123456789ABCDEF0123456789abcdef", "abc", "g" * 32, None, 5, "0" * 33):
        assert not is_job_id(bad)
