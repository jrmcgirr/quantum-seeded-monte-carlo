"""Qrypt response parsing, HKDF master seed and shard generators (HANDOFF §7.1–7.2).

Invariant I1: this module imports none of os, random, secrets, time (a test checks).
NumPy is imported lazily inside ``shard_generator`` so the API never loads it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from qmc_core.errors import EntropyError, ErrorCode

BLOCK_BYTES = 1024
KDF_NAME = "HKDF-SHA256"
KDF_VERSION = "qmc/master-seed/v1"
KDF_SALT = b"qmc/hkdf-salt/v1"
BITGEN = "PCG64DXSM"

_STATUS_CODES = {
    400: ErrorCode.QRYPT_BAD_REQUEST,
    401: ErrorCode.QRYPT_AUTH_FAILED,
    403: ErrorCode.QRYPT_FORBIDDEN,
    429: ErrorCode.QRYPT_RATE_LIMITED,
    503: ErrorCode.QRYPT_ENTROPY_UNAVAILABLE,
}
_SEED_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class MasterSeed:
    hex: str  # 64 lowercase hex characters
    int: int  # int.from_bytes(okm, "big")
    raw_sha256: str  # sha256 of the Qrypt block (the block itself is never stored)
    kdf: str = KDF_NAME
    kdf_version: str = KDF_VERSION


def _bad(why: str) -> EntropyError:
    return EntropyError(
        ErrorCode.QRYPT_BAD_RESPONSE, f"Qrypt returned an unusable response: {why}."
    )


def _check_block(block: bytes) -> None:
    if len(block) != BLOCK_BYTES:
        raise _bad(f"block is {len(block)} bytes, expected {BLOCK_BYTES}")
    if not any(block):
        raise _bad("block is all zero")


def parse_qrypt_response(status: int, body: bytes) -> bytes:
    """Map a Qrypt HTTP response to the decoded 1,024-byte block.

    Raises ``EntropyError`` with the §5.3 code: 400 → QRYPT_BAD_REQUEST, 401 →
    QRYPT_AUTH_FAILED, 403 → QRYPT_FORBIDDEN, 429 → QRYPT_RATE_LIMITED, 503 →
    QRYPT_ENTROPY_UNAVAILABLE, other 5xx → QRYPT_SERVER_ERROR; a 200 that is not JSON, lacks
    ``random`` as a list of exactly one string, has invalid base64, ≠ 1,024 bytes, an
    all-zero block or a present ``size`` other than 1 → QRYPT_BAD_RESPONSE.
    """
    if status in _STATUS_CODES:
        raise EntropyError(_STATUS_CODES[status])
    if 500 <= status <= 599:
        raise EntropyError(ErrorCode.QRYPT_SERVER_ERROR, f"Qrypt server error ({status}).")
    if status != 200:
        raise _bad(f"HTTP {status}")
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise _bad("body is not JSON") from None
    if not isinstance(doc, dict):
        raise _bad("body is not a JSON object")
    if "size" in doc and (isinstance(doc["size"], bool) or doc["size"] != 1):
        raise _bad("size is not 1")
    blocks = doc.get("random")
    if not (isinstance(blocks, list) and len(blocks) == 1 and isinstance(blocks[0], str)):
        raise _bad("random is not a list of exactly one string")
    try:
        block = base64.b64decode(blocks[0], validate=True)
    except (binascii.Error, ValueError):
        raise _bad("invalid base64") from None
    _check_block(block)
    return block


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF-SHA256 (extract then expand), standard library only."""
    if not 0 < length <= 255 * 32:
        raise ValueError("length out of range")
    prk = hmac.new(salt or bytes(32), ikm, hashlib.sha256).digest()
    okm, t = b"", b""
    for counter in range(1, -(-length // 32) + 1):
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def derive_master_seed(block: bytes, job_id: str) -> MasterSeed:
    """HKDF-SHA256 over the full block, salt ``KDF_SALT``, info ``qmc/master-seed/v1|<job_id>``.

    Raises ``EntropyError`` (QRYPT_BAD_RESPONSE) if the block is not 1,024 bytes or all zero.
    """
    _check_block(block)
    info = KDF_VERSION.encode("ascii") + b"|" + job_id.encode("ascii")
    okm = hkdf_sha256(block, KDF_SALT, info, 32)
    return MasterSeed(
        hex=okm.hex(),
        int=int.from_bytes(okm, "big"),
        raw_sha256=hashlib.sha256(block).hexdigest(),
    )


def master_int_from_hex(master_seed_hex: str) -> int:
    """The integer form of a stored ``master_seed_hex`` (64 lowercase hex; else ValueError)."""
    if not (isinstance(master_seed_hex, str) and _SEED_HEX.fullmatch(master_seed_hex)):
        raise ValueError("master_seed_hex must be 64 lowercase hex characters")
    return int(master_seed_hex, 16)


def shard_generator(master_int: int, shard_index: int):
    """``Generator(PCG64DXSM(SeedSequence(master_int, spawn_key=(shard_index,))))``."""
    import numpy as np

    seq = np.random.SeedSequence(master_int, spawn_key=(shard_index,))
    return np.random.Generator(np.random.PCG64DXSM(seq))
