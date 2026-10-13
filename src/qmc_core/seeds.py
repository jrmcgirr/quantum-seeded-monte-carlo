"""Qrypt response parsing, HKDF master seed and shard generators (HANDOFF §7.1–7.2).

Invariant I1: this module imports none of os, random, secrets, time (a test checks).
NumPy is imported lazily inside ``shard_generator`` so the API never loads it.
"""

from __future__ import annotations

from dataclasses import dataclass

BLOCK_BYTES = 1024
KDF_NAME = "HKDF-SHA256"
KDF_VERSION = "qmc/master-seed/v1"
KDF_SALT = b"qmc/hkdf-salt/v1"
BITGEN = "PCG64DXSM"


@dataclass(frozen=True)
class MasterSeed:
    hex: str  # 64 lowercase hex characters
    int: int  # int.from_bytes(okm, "big")
    raw_sha256: str  # sha256 of the Qrypt block (the block itself is never stored)
    kdf: str = KDF_NAME
    kdf_version: str = KDF_VERSION


def parse_qrypt_response(status: int, body: bytes) -> bytes:
    """Map a Qrypt HTTP response to the decoded 1,024-byte block.

    Raises ``EntropyError`` with the §5.3 code: 400 → QRYPT_BAD_REQUEST, 401 →
    QRYPT_AUTH_FAILED, 403 → QRYPT_QUOTA_EXHAUSTED, 429 → QRYPT_RATE_LIMITED, 503 →
    QRYPT_ENTROPY_UNAVAILABLE, other 5xx → QRYPT_SERVER_ERROR; a 200 that is not JSON, lacks
    ``random`` as a list of exactly one string, has invalid base64, ≠ 1,024 bytes, an
    all-zero block or a present ``size`` other than 1 → QRYPT_BAD_RESPONSE.
    """
    raise NotImplementedError


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF-SHA256 (extract then expand), standard library only."""
    raise NotImplementedError


def derive_master_seed(block: bytes, job_id: str) -> MasterSeed:
    """HKDF-SHA256 over the full block, salt ``KDF_SALT``, info ``qmc/master-seed/v1|<job_id>``.

    Raises ``EntropyError`` (QRYPT_BAD_RESPONSE) if the block is not 1,024 bytes or all zero.
    """
    raise NotImplementedError


def master_int_from_hex(master_seed_hex: str) -> int:
    """The integer form of a stored ``master_seed_hex`` (64 lowercase hex; else ValueError)."""
    raise NotImplementedError


def shard_generator(master_int: int, shard_index: int):
    """``Generator(PCG64DXSM(SeedSequence(master_int, spawn_key=(shard_index,))))``."""
    raise NotImplementedError
