"""Qrypt entropy sources (HANDOFF §7.5 step 4): the HTTP client and the fake used by every test.

``EntropySource.fetch`` owns the policy shared by both: one overall deadline, at most one
jittered retry (429/5xx/network only, within 1 s, after a fresh limiter slot), and a
``qrypt_call`` log line plus ``QryptLatencyMs`` for every attempt. Implementations supply only
``request`` (one HTTP exchange). Response parsing is ``qmc_core.seeds.parse_qrypt_response``.

The token, the Authorization header and the block are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from qmc_api import aws
from qmc_core import codec, emf, logjson
from qmc_core.errors import EntropyError, ErrorCode
from qmc_core.seeds import MasterSeed, parse_qrypt_response

RETRYABLE = frozenset(
    {
        ErrorCode.QRYPT_RATE_LIMITED,
        ErrorCode.QRYPT_SERVER_ERROR,
        ErrorCode.QRYPT_ENTROPY_UNAVAILABLE,
        ErrorCode.QRYPT_TIMEOUT,
        ErrorCode.QRYPT_UNREACHABLE,
    }
)
RETRY_WITHIN_S = 1.0
RETRY_PAUSE_S = (0.1, 0.3)


@dataclass(frozen=True)
class EntropyResult:
    block: bytes
    source: str
    endpoint: str
    http_status: int
    latency_ms: int  # the whole fetch, retry included
    fetched_at: str

    def provenance(self, seed: MasterSeed) -> dict:
        """``seed_provenance`` for the job item (§5.4). Ints and strings only."""
        return {
            "source": self.source,
            "endpoint": self.endpoint,
            "fetched_at": self.fetched_at,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "block_bytes": len(self.block),
            "raw_sha256": seed.raw_sha256,
            "kdf": seed.kdf,
            "kdf_version": seed.kdf_version,
        }


def _ms_since(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


class EntropySource:
    source = "qrypt"
    endpoint = "unknown"

    def request(self, token: str, deadline: float) -> tuple[int, bytes]:
        """One HTTP exchange before ``deadline`` (monotonic s) → ``(status, body)``.
        Transport failures raise ``EntropyError`` (QRYPT_TIMEOUT / QRYPT_UNREACHABLE)."""
        raise NotImplementedError

    def fetch(
        self, token: str, budget_ms: int, take_retry_slot: Callable[[], bool]
    ) -> EntropyResult:
        """One 1 KiB block within ``budget_ms``, or ``EntropyError`` with the §5.3 code."""
        start = time.monotonic()
        deadline = start + budget_ms / 1000
        attempt = 1
        while True:
            t0, status = time.monotonic(), None
            try:
                status, body = self.request(token, deadline)
                block = parse_qrypt_response(status, body)
            except EntropyError as exc:
                self._record(attempt, status, _ms_since(t0), exc.code.name)
                if (
                    attempt == 2
                    or exc.code not in RETRYABLE
                    or time.monotonic() - start >= RETRY_WITHIN_S
                ):
                    raise
                time.sleep(random.uniform(*RETRY_PAUSE_S))
                if not take_retry_slot():
                    raise  # the cause is Qrypt, not our limiter (HANDOFF §4.1)
                attempt = 2
                continue
            self._record(attempt, status, _ms_since(t0), "ok")
            return EntropyResult(
                block=block,
                source=self.source,
                endpoint=self.endpoint,
                http_status=status,
                latency_ms=_ms_since(start),
                fetched_at=codec.iso_ms(time.time()),
            )

    @staticmethod
    def _record(attempt: int, status: int | None, latency_ms: int, outcome: str) -> None:
        logjson.log(
            "qrypt_call",
            http_status=status,
            latency_ms=latency_ms,
            attempt=attempt,
            outcome=outcome,
        )
        emf.emit("QryptLatencyMs", latency_ms, attempt=attempt, outcome=outcome)


class QryptHttpSource(EntropySource):
    """``GET <url>?size=1`` over one keep-alive connection reused across warm invocations."""

    def __init__(self, url: str) -> None:
        u = urlsplit(url)
        self._https = u.scheme == "https"
        self._host, self._port = u.hostname, u.port
        self._target = f"{u.path}?size=1"
        self.endpoint = u.hostname.split(".")[0]  # "api-eus"
        self._conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def request(self, token: str, deadline: float) -> tuple[int, bytes]:
        for reopen in (False, True):
            reused = self._conn is not None and self._conn.sock is not None
            try:
                status, body = self._exchange(token, deadline)
            except (ConnectionResetError, BrokenPipeError):  # incl. RemoteDisconnected
                self.close()
                if reused and not reopen:
                    continue  # idle keep-alive closed by the server: reopen once, not a retry
                raise EntropyError(ErrorCode.QRYPT_UNREACHABLE) from None
            except TimeoutError:
                self.close()
                raise EntropyError(ErrorCode.QRYPT_TIMEOUT) from None
            except (OSError, http.client.HTTPException):  # DNS, TLS, refused, bad status line
                self.close()
                raise EntropyError(ErrorCode.QRYPT_UNREACHABLE) from None
            except EntropyError:
                self.close()
                raise
            return status, body
        raise AssertionError("unreachable")

    def _exchange(self, token: str, deadline: float) -> tuple[int, bytes]:
        # http.client timeouts are per socket operation: re-derive each from the time left.
        # ponytail: resp.read() may take several recvs, each up to the time left; fine for ~1.4 KB.
        if self._conn is None:
            cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            self._conn = cls(self._host, self._port, timeout=_left(deadline))
        conn = self._conn
        conn.timeout = _left(deadline)
        if conn.sock is not None:
            conn.sock.settimeout(conn.timeout)
        conn.request(
            "GET",
            self._target,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )
        conn.sock.settimeout(_left(deadline))
        resp = conn.getresponse()
        if conn.sock is not None:
            conn.sock.settimeout(_left(deadline))
        body = resp.read()
        if resp.will_close:
            self.close()
        return resp.status, body


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise EntropyError(ErrorCode.QRYPT_TIMEOUT)
    return left


# A response that makes parse_qrypt_response raise each HTTP-level code.
_FAULTS: dict[ErrorCode, tuple[int, bytes]] = {
    ErrorCode.QRYPT_BAD_REQUEST: (400, b"{}"),
    ErrorCode.QRYPT_AUTH_FAILED: (401, b"{}"),
    ErrorCode.QRYPT_QUOTA_EXHAUSTED: (403, b"{}"),
    ErrorCode.QRYPT_RATE_LIMITED: (429, b"{}"),
    ErrorCode.QRYPT_SERVER_ERROR: (500, b"{}"),
    ErrorCode.QRYPT_ENTROPY_UNAVAILABLE: (503, b"{}"),
    ErrorCode.QRYPT_BAD_RESPONSE: (200, b"not json"),
}
_TRANSPORT = (ErrorCode.QRYPT_TIMEOUT, ErrorCode.QRYPT_UNREACHABLE)


class FakeEntropySource(EntropySource):
    """The assignment's mock Qrypt client: deterministic blocks, scripted failures.

    Block k is ``SHAKE-256(seed || k as 8 bytes)[:1024]``, so two fakes with the same seed yield
    the same blocks (the reused-block test). ``failures`` is consumed one per request: an
    ``ErrorCode`` (any Qrypt code), a raw ``(status, body)`` pair, or None for a good block.
    ``calls`` counts requests.
    """

    source = "fake"
    endpoint = "fake"

    def __init__(
        self,
        seed: bytes = b"qmc-fake-entropy",
        failures: Iterable[ErrorCode | tuple[int, bytes] | None] = (),
    ) -> None:
        self.seed = seed
        self.failures = list(failures)
        self.calls = 0
        self._k = 0

    def next_block(self) -> bytes:
        block = hashlib.shake_256(self.seed + self._k.to_bytes(8, "big")).digest(1024)
        self._k += 1
        return block

    def request(self, token: str, deadline: float) -> tuple[int, bytes]:
        self.calls += 1
        fault = self.failures.pop(0) if self.failures else None
        if isinstance(fault, tuple):
            return fault
        if fault in _TRANSPORT:
            raise EntropyError(fault)
        if fault is not None:
            return _FAULTS[fault]
        body = {"random": [base64.b64encode(self.next_block()).decode()], "size": 1}
        return 200, json.dumps(body).encode()


_source: EntropySource | None = None


def set_entropy_source(source: EntropySource | None) -> None:
    """Inject a source (tests); None restores ``QryptHttpSource`` on next use."""
    global _source
    _source = source


def get_entropy_source() -> EntropySource:
    """The only way submit obtains its source."""
    global _source
    if _source is None:
        _source = QryptHttpSource(aws.env("QRYPT_URL"))
    return _source
