import ast
import base64
import hashlib
import json
import pathlib

import numpy as np
import pytest

import qmc_core.seeds as seeds
from qmc_core.errors import EntropyError, ErrorCode
from qmc_core.seeds import (
    derive_master_seed,
    hkdf_sha256,
    master_int_from_hex,
    parse_qrypt_response,
    shard_generator,
)

BLOCK = hashlib.shake_256(b"test block").digest(1024)
JOB = "0123456789abcdef0123456789abcdef"


def body(block=BLOCK, **extra):
    return json.dumps({"random": [base64.b64encode(block).decode()], "size": 1, **extra}).encode()


def test_rfc5869_case_1():
    okm = hkdf_sha256(
        bytes.fromhex("0b" * 22),
        bytes.fromhex("000102030405060708090a0b0c"),
        bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
        42,
    )
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    )


def test_rfc5869_case_3_empty_salt_and_info():
    okm = hkdf_sha256(bytes.fromhex("0b" * 22), b"", b"", 42)
    assert okm.hex().startswith("8da4e775a563c18f715f802a063c5a31")
    with pytest.raises(ValueError):
        hkdf_sha256(b"x", b"", b"", 0)


def test_master_seed_deterministic_and_bound_to_job():
    a, b = derive_master_seed(BLOCK, JOB), derive_master_seed(BLOCK, JOB)
    assert a == b and len(a.hex) == 64 and a.int == int(a.hex, 16)
    assert a.raw_sha256 == hashlib.sha256(BLOCK).hexdigest()
    assert (a.kdf, a.kdf_version) == ("HKDF-SHA256", "qmc/master-seed/v1")
    assert derive_master_seed(BLOCK, "f" * 32).hex != a.hex
    other = bytearray(BLOCK)
    other[-1] ^= 1
    assert derive_master_seed(bytes(other), JOB).hex != a.hex


def test_master_seed_matches_spec_formula():
    import hmac

    prk = hmac.new(b"qmc/hkdf-salt/v1", BLOCK, hashlib.sha256).digest()
    t1 = hmac.new(prk, b"qmc/master-seed/v1|" + JOB.encode() + b"\x01", hashlib.sha256).digest()
    assert derive_master_seed(BLOCK, JOB).hex == t1.hex()


@pytest.mark.parametrize("block", [b"\x00" * 1024, b"\x01" * 1023, b"\x01" * 1025, b""])
def test_master_seed_rejects_bad_blocks(block):
    with pytest.raises(EntropyError) as e:
        derive_master_seed(block, JOB)
    assert e.value.code is ErrorCode.QRYPT_BAD_RESPONSE


def test_parse_ok():
    assert parse_qrypt_response(200, body()) == BLOCK
    no_size = json.dumps({"random": [base64.b64encode(BLOCK).decode()], "extra": 1}).encode()
    assert parse_qrypt_response(200, no_size) == BLOCK


@pytest.mark.parametrize(
    "status,code",
    [
        (400, ErrorCode.QRYPT_BAD_REQUEST),
        (401, ErrorCode.QRYPT_AUTH_FAILED),
        (403, ErrorCode.QRYPT_QUOTA_EXHAUSTED),
        (429, ErrorCode.QRYPT_RATE_LIMITED),
        (500, ErrorCode.QRYPT_SERVER_ERROR),
        (502, ErrorCode.QRYPT_SERVER_ERROR),
        (503, ErrorCode.QRYPT_ENTROPY_UNAVAILABLE),
        (504, ErrorCode.QRYPT_SERVER_ERROR),
        (404, ErrorCode.QRYPT_BAD_RESPONSE),
        (204, ErrorCode.QRYPT_BAD_RESPONSE),
    ],
)
def test_parse_status_mapping(status, code):
    with pytest.raises(EntropyError) as e:
        parse_qrypt_response(status, body())
    assert e.value.code is code


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff",
        b"[1]",
        b"{}",
        json.dumps({"random": []}).encode(),
        json.dumps({"random": ["a", "b"]}).encode(),
        json.dumps({"random": [1]}).encode(),
        json.dumps({"random": "abc"}).encode(),
        json.dumps({"random": ["!!!notbase64"]}).encode(),
        body(BLOCK[:1000]),
        body(b"\x00" * 1024),
        body(size=2),
        json.dumps({"random": [base64.b64encode(BLOCK).decode()], "size": True}).encode(),
    ],
)
def test_parse_malformed(raw):
    with pytest.raises(EntropyError) as e:
        parse_qrypt_response(200, raw)
    assert e.value.code is ErrorCode.QRYPT_BAD_RESPONSE
    assert e.value.http_status == 502


def test_seeds_imports_no_local_entropy():
    tree = ast.parse(pathlib.Path(seeds.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"os", "random", "secrets", "time", "uuid"}, names


def test_spawn_key_equals_spawn():
    m = derive_master_seed(BLOCK, JOB).int
    spawned = np.random.SeedSequence(m).spawn(50)
    for i in range(50):
        direct = np.random.SeedSequence(m, spawn_key=(i,))
        assert direct.generate_state(8).tolist() == spawned[i].generate_state(8).tolist()
        a = shard_generator(m, i).standard_normal(4)
        b = np.random.Generator(np.random.PCG64DXSM(spawned[i])).standard_normal(4)
        assert a.tobytes() == b.tobytes()


def test_spawn_is_stateful_so_we_never_use_it():
    seq = np.random.SeedSequence(1)
    first, second = seq.spawn(1)[0], seq.spawn(1)[0]
    assert first.spawn_key != second.spawn_key  # a second call continues numbering


def test_shard_streams_distinct_and_reproducible():
    m = derive_master_seed(BLOCK, JOB).int
    states = {
        np.random.SeedSequence(m, spawn_key=(i,)).generate_state(4).tobytes() for i in range(1000)
    }
    assert len(states) == 1000
    assert shard_generator(m, 7).random(3).tobytes() == shard_generator(m, 7).random(3).tobytes()
    assert type(shard_generator(m, 0).bit_generator).__name__ == "PCG64DXSM"


def test_master_int_from_hex():
    s = derive_master_seed(BLOCK, JOB)
    assert master_int_from_hex(s.hex) == s.int
    for bad in ("ab", s.hex.upper(), None, s.hex + "0"):
        with pytest.raises(ValueError):
            master_int_from_hex(bad)
