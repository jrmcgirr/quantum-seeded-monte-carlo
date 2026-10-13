"""API router Lambda behind the Function URL (payload 2.0, HANDOFF §5).

Handlers never raise: every exception becomes a §5.3 response. Request headers are never logged.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal

from qmc_api import aws, jobs, pending, submit
from qmc_core import ddb, logjson
from qmc_core.errors import ErrorCode, QmcError

logjson.configure("api")
logjson.log("config", **aws.config())


def health(event, context) -> tuple[int, dict]:
    checks = {}
    try:
        aws.table().get_item(**ddb.health_get())
        checks["dynamodb"] = "ok"
    except Exception as exc:
        checks["dynamodb"] = f"error: {type(exc).__name__}"
    try:
        aws.ssm().get_parameter(Name=aws.env("QRYPT_API_KEY_PARAM"), WithDecryption=False)
        checks["ssm"] = "ok"
    except Exception as exc:
        checks["ssm"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return (200 if ok else 503), {
        "status": "ok" if ok else "error",
        "checks": checks,
        "build_sha": aws.env("QMC_BUILD_SHA"),
        "env": aws.env("QMC_ENV"),
    }


# (path pattern, {method: handler}); captured groups are passed to the handler.
ROUTES = [
    (re.compile(r"/jobs"), {"POST": submit.submit, "GET": jobs.list_jobs}),
    (re.compile(r"/jobs/([^/]+)"), {"GET": jobs.get_job}),
    (re.compile(r"/jobs/([^/]+)/replay"), {"POST": submit.replay}),
    (re.compile(r"/health"), {"GET": health}),
]


def route(event, context) -> tuple[int, dict]:
    method = event["requestContext"]["http"]["method"].upper()
    path = event.get("rawPath") or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    for pattern, methods in ROUTES:
        m = pattern.fullmatch(path)
        if m:
            if method not in methods:
                raise QmcError(ErrorCode.METHOD_NOT_ALLOWED, f"{method} is not allowed on {path}.")
            return methods[method](event, context, *m.groups())
    raise QmcError(ErrorCode.ROUTE_NOT_FOUND, f"No route for {path}.")


def _json_default(o):
    if isinstance(o, Decimal):  # DynamoDB numbers; every stored number is an integer
        return int(o) if o == o.to_integral_value() else str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def handle(event, context):
    request_id = getattr(context, "aws_request_id", "local")
    logjson.clear()
    logjson.bind(request_id=request_id)
    if isinstance(event, dict) and "qmc_seed" in event and "requestContext" not in event:
        pending.run(event, context)  # async PENDING_ENTROPY seeding (contract change 008)
        return None
    t0 = time.monotonic()
    headers = {"content-type": "application/json", "x-request-id": request_id}
    try:
        status, body = route(event, context)
    except QmcError as exc:
        status, body = exc.http_status, exc.to_body()
        if exc.retry_after is not None:
            headers["retry-after"] = str(exc.retry_after)
        elif exc.code is ErrorCode.QRYPT_RATE_LIMITED:
            headers["retry-after"] = "10"
    except Exception as exc:
        logjson.log("internal_error", level="ERROR", error=type(exc).__name__, detail=str(exc))
        err = QmcError(ErrorCode.INTERNAL_ERROR, f"Internal error (request id {request_id}).")
        status, body = err.http_status, err.to_body()
    try:
        payload = json.dumps(body, default=_json_default)
    except Exception as exc:
        logjson.log("internal_error", level="ERROR", error=type(exc).__name__, detail=str(exc))
        status = 500
        payload = json.dumps(
            QmcError(
                ErrorCode.INTERNAL_ERROR, f"Internal error (request id {request_id})."
            ).to_body()
        )
    logjson.log(
        "request",
        method=event.get("requestContext", {}).get("http", {}).get("method"),
        path=event.get("rawPath"),
        status=status,
        latency_ms=round((time.monotonic() - t0) * 1000),
    )
    return {"statusCode": status, "headers": headers, "body": payload}
