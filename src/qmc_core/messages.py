"""SQS message v1 (HANDOFF §6.3): strict encode/decode, one message per shard."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields

from qmc_core.params import PARAM_NAMES

VERSION = 1
BATCH_SIZE = 10
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class MessageError(ValueError):
    """A body that is not a valid v1 shard message (the worker logs ``poison_message``)."""


@dataclass(frozen=True)
class ShardMessage:
    job_id: str
    shard_index: int
    n_shards: int
    shard_paths: int  # this shard's paths (pairs × 2 for antithetic), never the job total
    params: Mapping[str, str]  # repr strings
    master_seed_hex: str
    chunk_size: int
    variance_reduction: str
    algo_version: str
    fail: bool = False
    v: int = VERSION


def encode(msg: ShardMessage) -> str:
    """Compact JSON (``separators=(",", ":")``)."""
    doc = asdict(msg)
    doc["params"] = dict(msg.params)
    return json.dumps(doc, separators=(",", ":"))


def _int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _finite_repr(x: object) -> bool:
    try:
        return isinstance(x, str) and math.isfinite(float(x))
    except ValueError:
        return False


def decode(body: str | bytes) -> ShardMessage:
    """Strict: every field present with the right JSON type, ``v == 1``, no unknown fields,
    ``0 ≤ shard_index < n_shards``, 64-hex seed, 32-hex job id. Raises ``MessageError``."""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise MessageError(f"body is not JSON: {e}") from None
    if not isinstance(doc, dict):
        raise MessageError("body is not a JSON object")
    expected = {f.name for f in fields(ShardMessage)}
    if set(doc) != expected:
        raise MessageError(
            f"fields differ: missing {sorted(expected - set(doc))}, unknown {sorted(set(doc) - expected)}"
        )
    params = doc["params"]
    checks = [
        (doc["v"] == VERSION and _int(doc["v"]), "v must be 1"),
        (isinstance(doc["job_id"], str) and _HEX32.fullmatch(doc["job_id"]), "bad job_id"),
        (_int(doc["n_shards"]) and doc["n_shards"] >= 1, "bad n_shards"),
        (
            _int(doc["shard_index"])
            and _int(doc["n_shards"])
            and 0 <= doc["shard_index"] < doc["n_shards"],
            "bad shard_index",
        ),
        (_int(doc["shard_paths"]) and doc["shard_paths"] >= 1, "bad shard_paths"),
        (
            isinstance(params, dict)
            and set(params) == set(PARAM_NAMES)
            and all(_finite_repr(v) for v in params.values()),
            "bad params",
        ),
        (
            isinstance(doc["master_seed_hex"], str) and _HEX64.fullmatch(doc["master_seed_hex"]),
            "bad master_seed_hex",
        ),
        (_int(doc["chunk_size"]) and doc["chunk_size"] >= 1, "bad chunk_size"),
        (doc["variance_reduction"] in ("none", "antithetic"), "bad variance_reduction"),
        (isinstance(doc["algo_version"], str) and doc["algo_version"], "bad algo_version"),
        (isinstance(doc["fail"], bool), "fail must be a boolean"),
    ]
    for ok, problem in checks:
        if not ok:
            raise MessageError(problem)
    return ShardMessage(**doc)


def for_job(job_item: Mapping) -> list[ShardMessage]:
    """One message per shard from a job item; ``fail`` is true for ``fault_injection.fail_shards``."""
    fail = {int(i) for i in (job_item.get("fault_injection") or {}).get("fail_shards", ())}
    per_shard = [int(p) for p in job_item["paths_per_shard"]]
    return [
        ShardMessage(
            job_id=job_item["job_id"],
            shard_index=i,
            n_shards=len(per_shard),
            shard_paths=p,
            params={k: str(job_item["params"][k]) for k in PARAM_NAMES},
            master_seed_hex=job_item["master_seed_hex"],
            chunk_size=int(job_item["chunk_size"]),
            variance_reduction=job_item["variance_reduction"],
            algo_version=job_item["algo_version"],
            fail=i in fail,
        )
        for i, p in enumerate(per_shard)
    ]


def batch_entries(messages: list[ShardMessage]) -> list[list[dict]]:
    """``SendMessageBatch`` entries in groups of 10: ``{"Id": "<shard_index>", "MessageBody": ...}``."""
    entries = [{"Id": str(m.shard_index), "MessageBody": encode(m)} for m in messages]
    return [entries[i : i + BATCH_SIZE] for i in range(0, len(entries), BATCH_SIZE)]
