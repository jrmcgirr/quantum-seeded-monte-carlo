"""SQS message v1 (HANDOFF §6.3): strict encode/decode, one message per shard."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

VERSION = 1
BATCH_SIZE = 10


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
    raise NotImplementedError


def decode(body: str | bytes) -> ShardMessage:
    """Strict: every field present with the right JSON type, ``v == 1``, no unknown fields,
    ``0 ≤ shard_index < n_shards``, 64-hex seed, 32-hex job id. Raises ``MessageError``."""
    raise NotImplementedError


def for_job(job_item: Mapping) -> list[ShardMessage]:
    """One message per shard from a job item; ``fail`` is true for ``fault_injection.fail_shards``."""
    raise NotImplementedError


def batch_entries(messages: list[ShardMessage]) -> list[list[dict]]:
    """``SendMessageBatch`` entries in groups of 10: ``{"Id": "<shard_index>", "MessageBody": ...}``."""
    raise NotImplementedError
