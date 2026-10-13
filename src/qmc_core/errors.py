"""Error codes (HANDOFF §5.3) and the exceptions that carry them."""

from __future__ import annotations

from enum import Enum


class ErrorCode(Enum):
    """Each member's value is ``(http_status, default_message)``."""

    VALIDATION_ERROR = (400, "The request failed validation.")
    INVALID_JSON = (400, "The body is not valid JSON.")
    BODY_TOO_LARGE = (400, "The body is larger than 16 KiB.")
    UNSUPPORTED = (400, "The requested feature is not supported.")
    FAULT_INJECTION_DISABLED = (400, "Fault injection is disabled in this deployment.")
    JOB_NOT_FOUND = (404, "No such job.")
    ROUTE_NOT_FOUND = (404, "No such route.")
    METHOD_NOT_ALLOWED = (405, "Method not allowed on this route.")
    JOB_NOT_REPLAYABLE = (409, "Only a COMPLETE job can be replayed.")
    ENTROPY_RATE_LIMITED = (
        429,
        "Qrypt calls are spaced at least 2 s apart; retry after the Retry-After seconds.",
    )
    QRYPT_AUTH_FAILED = (503, "Qrypt rejected the token (401): invalid or expired.")
    # Qrypt sends the same 403 {"error": "Forbidden or entropy quota reached"} for a rejected token and an
    # exhausted quota, so the two cannot be told apart (contract change 003).
    QRYPT_FORBIDDEN = (
        503,
        "Qrypt refused the request (403): the token is invalid or expired, "
        "or the entropy quota is used up.",
    )
    QRYPT_RATE_LIMITED = (503, "Qrypt rate limit hit (429); not retried.")
    QRYPT_SERVER_ERROR = (503, "Qrypt server error (5xx).")
    QRYPT_ENTROPY_UNAVAILABLE = (503, "Qrypt entropy unavailable (503).")
    QRYPT_TIMEOUT = (503, "Qrypt did not answer within the deadline.")
    QRYPT_UNREACHABLE = (503, "Qrypt could not be reached (DNS, TLS or connection failure).")
    QRYPT_BAD_RESPONSE = (502, "Qrypt returned an unusable entropy response.")
    QRYPT_BAD_REQUEST = (500, "Qrypt rejected our request (400).")
    SECRET_UNAVAILABLE = (503, "The Qrypt token could not be read from SSM.")
    ENQUEUE_FAILED = (503, "The job could not be enqueued; it is marked FAILED.")
    INTERNAL_ERROR = (500, "Internal error.")

    @property
    def http_status(self) -> int:
        return self.value[0]

    @property
    def default_message(self) -> str:
        return self.value[1]


# Contract change 008: failures that an opt-in (on_entropy_failure="queue") job may retry later.
RETRYABLE_ENTROPY_CODES = frozenset(
    {
        ErrorCode.ENTROPY_RATE_LIMITED,
        ErrorCode.QRYPT_RATE_LIMITED,
        ErrorCode.QRYPT_SERVER_ERROR,
        ErrorCode.QRYPT_ENTROPY_UNAVAILABLE,
        ErrorCode.QRYPT_TIMEOUT,
        ErrorCode.QRYPT_UNREACHABLE,
    }
)


class QmcError(Exception):
    """An error with a §5.3 code. Handlers map it to the HTTP response with ``to_body()``."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        details: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.message = message or code.default_message
        self.details = details
        self.retry_after = retry_after
        super().__init__(f"{code.name}: {self.message}")

    @property
    def http_status(self) -> int:
        return self.code.http_status

    def to_body(self) -> dict:
        """``{"error": {"code", "message", "details"?}}`` (details only when present)."""
        error: dict = {"code": self.code.name, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class EntropyError(QmcError):
    """A Qrypt response or block that must not seed a job (code is one of the QRYPT_* codes)."""
