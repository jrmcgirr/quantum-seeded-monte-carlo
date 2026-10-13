"""The Qrypt token from SSM, cached at module level for SECRET_CACHE_TTL_S (HANDOFF §8.4).

The token never reaches a log line, an exception message or a response.
"""

from __future__ import annotations

import time

from qmc_api import aws
from qmc_core import logjson
from qmc_core.errors import ErrorCode, QmcError

_cache: dict[str, tuple[str, float]] = {}  # parameter name -> (token, fetched monotonic)


def get_token() -> str:
    name = aws.env("QRYPT_API_KEY_PARAM")
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[1] < aws.env_int("SECRET_CACHE_TTL_S"):
        return hit[0]
    try:
        token = aws.ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except Exception as exc:
        # The exception type only: botocore messages are safe, but nothing here needs them.
        logjson.log("secret_unavailable", level="ERROR", param=name, error=type(exc).__name__)
        raise QmcError(ErrorCode.SECRET_UNAVAILABLE) from None
    _cache[name] = (token, time.monotonic())
    return token


def clear() -> None:
    _cache.clear()
