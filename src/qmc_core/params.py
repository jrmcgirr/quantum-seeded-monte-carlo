"""Strict request validation (HANDOFF §5.2) and the shard path split."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

MAX_BODY_BYTES = 16 * 1024
PARAM_NAMES = ("S0", "K", "r", "sigma", "T")


@dataclass(frozen=True)
class Limits:
    max_paths: int = 10_000_000
    max_shards: int = 50
    max_paths_per_shard: int = 1_000_000

    @classmethod
    def from_env(cls) -> Limits:
        """Read ``MAX_PATHS``, ``MAX_SHARDS``, ``MAX_PATHS_PER_SHARD`` (defaults above)."""
        raise NotImplementedError


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

    def params(self) -> dict[str, float]:
        """``{"S0", "K", "r", "sigma", "T"}`` as floats."""
        raise NotImplementedError


def parse_body(raw: bytes | str | None, is_base64: bool = False) -> dict:
    """Decode a Function URL body into a JSON object.

    Raises ``QmcError``: ``BODY_TOO_LARGE`` over 16 KiB, ``INVALID_JSON`` for invalid JSON
    (including NaN/Infinity literals) or a non-object ("body must be a JSON object").
    """
    raise NotImplementedError


def validate_job_request(body: Mapping, limits: Limits, fault_injection_enabled: bool) -> JobSpec:
    """Validate a parsed body (§5.2), collecting every failure.

    Raises ``QmcError``: ``VALIDATION_ERROR`` with ``details`` (every failed field, unknown
    fields as ``problem: "unknown field"``); otherwise ``UNSUPPORTED`` (antithetic, until
    QMC-108) or ``FAULT_INJECTION_DISABLED``. Booleans are not numbers; ``1e6`` and
    ``1000000.0`` are not integers.
    """
    raise NotImplementedError


def split_paths(paths: int, shards: int, variance_reduction: str = "none") -> list[int]:
    """Per-shard path counts: shard i gets ``paths // shards`` plus one if ``i < paths % shards``.

    For ``"antithetic"`` the split applies to pairs (``paths // 2``) and each count is
    ``2 × pairs_i``.
    """
    raise NotImplementedError


def is_job_id(value: object) -> bool:
    """True for exactly 32 lowercase hex characters (``uuid4().hex``)."""
    raise NotImplementedError
