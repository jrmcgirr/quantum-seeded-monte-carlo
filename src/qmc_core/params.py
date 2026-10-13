"""Strict request validation (HANDOFF §5.2) and the shard path split."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from qmc_core.errors import ErrorCode, QmcError

MAX_BODY_BYTES = 16 * 1024
ALGO_VERSION = "qmc-sim/v1"
CHUNK_SIZE = 131_072  # 2**17; never change for an existing ALGO_VERSION
PARAM_NAMES = ("S0", "K", "r", "sigma", "T")


@dataclass(frozen=True)
class Limits:
    max_paths: int = 10_000_000
    max_shards: int = 50
    max_paths_per_shard: int = 1_000_000

    @classmethod
    def from_env(cls) -> Limits:
        """Read ``MAX_PATHS``, ``MAX_SHARDS``, ``MAX_PATHS_PER_SHARD`` (defaults above)."""
        d = cls()
        return cls(
            max_paths=int(os.environ.get("MAX_PATHS", d.max_paths)),
            max_shards=int(os.environ.get("MAX_SHARDS", d.max_shards)),
            max_paths_per_shard=int(os.environ.get("MAX_PATHS_PER_SHARD", d.max_paths_per_shard)),
        )


@dataclass(frozen=True)
class FieldError:
    field: str
    problem: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "problem": self.problem}


@dataclass(frozen=True)
class JobSpec:
    """A validated request. Parameters are Python floats (JSON integers converted)."""

    S0: float
    K: float
    r: float
    sigma: float
    T: float
    paths: int
    shards: int
    variance_reduction: str = "none"
    fail_shards: tuple[int, ...] = field(default=())
    on_entropy_failure: str = "reject"  # contract change 008

    def params(self) -> dict[str, float]:
        """``{"S0", "K", "r", "sigma", "T"}`` as floats."""
        return {name: getattr(self, name) for name in PARAM_NAMES}


# (low, low_inclusive, high) per parameter; every high bound is inclusive.
_BOUNDS = {
    "S0": (0, False, 1e6),
    "K": (0, False, 1e6),
    "r": (-1, True, 1),
    "sigma": (0, False, 5),
    "T": (0, False, 50),
}
_FIELDS = {
    *PARAM_NAMES,
    "paths",
    "shards",
    "variance_reduction",
    "fault_injection",
    "on_entropy_failure",
}
_JOB_ID = re.compile(r"[0-9a-f]{32}")


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def parse_body(raw: bytes | str | None, is_base64: bool = False) -> dict:
    """Decode a Function URL body into a JSON object.

    Raises ``QmcError``: ``BODY_TOO_LARGE`` over 16 KiB, ``INVALID_JSON`` for invalid JSON
    (including NaN/Infinity literals) or a non-object ("body must be a JSON object").
    """
    data = raw or b""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if is_base64:
        try:
            data = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise QmcError(ErrorCode.INVALID_JSON, "body is not valid base64") from None
    if len(data) > MAX_BODY_BYTES:
        raise QmcError(ErrorCode.BODY_TOO_LARGE)
    try:
        body = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise QmcError(ErrorCode.INVALID_JSON) from None
    if not isinstance(body, dict):
        raise QmcError(ErrorCode.INVALID_JSON, "body must be a JSON object")
    return body


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _number(name: str, x: object, errors: list[FieldError]) -> float | None:
    if not (isinstance(x, (int, float)) and not isinstance(x, bool)):
        errors.append(FieldError(name, "must be a JSON number"))
        return None
    if isinstance(x, float) and not math.isfinite(x):
        errors.append(FieldError(name, "must be finite"))
        return None
    low, low_inclusive, high = _BOUNDS[name]
    above_low = x >= low if low_inclusive else x > low
    if not (above_low and x <= high):
        op = ">=" if low_inclusive else ">"
        errors.append(FieldError(name, f"must be {op} {low} and <= {high}"))
        return None
    return float(x)


def _integer(name: str, x: object, low: int, high: int, errors: list[FieldError]) -> int | None:
    if not _is_int(x):
        errors.append(FieldError(name, "must be a JSON integer"))
        return None
    if not low <= x <= high:
        errors.append(FieldError(name, f"must be >= {low} and <= {high}"))
        return None
    return x


def validate_job_request(body: Mapping, limits: Limits, fault_injection_enabled: bool) -> JobSpec:
    """Validate a parsed body (§5.2), collecting every failure.

    Raises ``QmcError``: ``VALIDATION_ERROR`` with ``details`` (every failed field, unknown
    fields as ``problem: "unknown field"``); otherwise ``FAULT_INJECTION_DISABLED``. Booleans are not numbers; ``1e6`` and
    ``1000000.0`` are not integers.
    """
    errors: list[FieldError] = []
    values: dict[str, float] = {}
    for name in PARAM_NAMES:
        if name not in body:
            errors.append(FieldError(name, "required"))
        elif (v := _number(name, body[name], errors)) is not None:
            values[name] = v

    paths = shards = None
    if "paths" not in body:
        errors.append(FieldError("paths", "required"))
    else:
        paths = _integer("paths", body["paths"], 2, limits.max_paths, errors)
    if "shards" not in body:
        errors.append(FieldError("shards", "required"))
    else:
        shards = _integer("shards", body["shards"], 1, limits.max_shards, errors)
    if paths is not None and shards is not None:
        if shards > paths:
            errors.append(FieldError("shards", "must be <= paths"))
        elif -(-paths // shards) > limits.max_paths_per_shard:
            errors.append(
                FieldError("paths", f"ceil(paths / shards) must be <= {limits.max_paths_per_shard}")
            )

    variance_reduction = body.get("variance_reduction", "none")
    if variance_reduction not in ("none", "antithetic"):
        errors.append(FieldError("variance_reduction", 'must be "none" or "antithetic"'))
    elif variance_reduction == "antithetic" and paths is not None and shards is not None:
        if paths % 2 or paths < 4:
            errors.append(FieldError("paths", "must be even and >= 4 for antithetic"))
        elif shards > paths // 2:
            errors.append(FieldError("shards", "must be <= paths / 2 for antithetic"))

    on_entropy_failure = body.get("on_entropy_failure", "reject")
    if on_entropy_failure not in ("reject", "queue"):
        errors.append(FieldError("on_entropy_failure", 'must be "reject" or "queue"'))

    fail_shards: tuple[int, ...] = ()
    fi = body.get("fault_injection")
    if "fault_injection" in body and fault_injection_enabled:
        listed = fi.get("fail_shards") if isinstance(fi, dict) else None
        if not isinstance(fi, dict) or set(fi) != {"fail_shards"} or not isinstance(listed, list):
            errors.append(FieldError("fault_injection", 'must be {"fail_shards": [int, ...]}'))
        elif not all(_is_int(i) and shards is not None and 0 <= i < shards for i in listed):
            errors.append(
                FieldError("fault_injection.fail_shards", "each must be an integer in [0, shards)")
            )
        else:
            fail_shards = tuple(sorted(set(listed)))

    errors.extend(FieldError(k, "unknown field") for k in sorted(set(body) - _FIELDS))

    if errors:
        raise QmcError(
            ErrorCode.VALIDATION_ERROR,
            f"{len(errors)} field(s) failed validation.",
            details=[e.to_dict() for e in errors],
        )
    if "fault_injection" in body and not fault_injection_enabled:
        raise QmcError(ErrorCode.FAULT_INJECTION_DISABLED)
    assert paths is not None and shards is not None
    return JobSpec(
        **values,
        paths=paths,
        shards=shards,
        variance_reduction=variance_reduction,
        on_entropy_failure=on_entropy_failure,
        fail_shards=fail_shards,
    )


def split_paths(paths: int, shards: int, variance_reduction: str = "none") -> list[int]:
    """Per-shard path counts: shard i gets ``paths // shards`` plus one if ``i < paths % shards``.

    For ``"antithetic"`` the split applies to pairs (``paths // 2``) and each count is
    ``2 × pairs_i``.
    """
    if variance_reduction == "antithetic":
        return [2 * p for p in split_paths(paths // 2, shards)]
    q, rem = divmod(paths, shards)
    return [q + (1 if i < rem else 0) for i in range(shards)]


def is_job_id(value: object) -> bool:
    """True for exactly 32 lowercase hex characters (``uuid4().hex``)."""
    return isinstance(value, str) and _JOB_ID.fullmatch(value) is not None
