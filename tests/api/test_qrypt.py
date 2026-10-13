"""QMC-121: QryptHttpSource against a local stub server, retry policy, secret cache, no token in logs."""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from qmc_api.qrypt import FakeEntropySource, QryptHttpSource
from qmc_core.errors import EntropyError, ErrorCode

TOKEN = "tok-THIS-MUST-NEVER-BE-LOGGED"
GOOD = json.dumps({"random": [base64.b64encode(bytes(range(256)) * 4).decode()], "size": 1})


class Stub:
    """Serves a scripted list of actions, one per request: ``(status, body)``, ``("sleep", s)``
    or ``("drop_after", (status, body))`` (answer, then close without ``Connection: close``)."""

    def __init__(self) -> None:
        self.actions: list = []
        self.requests: list[dict] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                stub.requests.append({"path": self.path, "auth": self.headers["Authorization"]})
                kind, arg = stub.actions.pop(0) if stub.actions else (200, GOOD)
                if kind == "sleep":
                    time.sleep(arg)
                    kind, arg = 200, GOOD
                drop = kind == "drop_after"
                if drop:
                    kind, arg = arg
                data = arg.encode()
                self.send_response(kind)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = drop

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1/quantum-entropy"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.server.shutdown()


def fetch(source, budget_ms=2500, slots=True):
    taken = []

    def take():
        taken.append(1)
        return slots

    return source.fetch(TOKEN, budget_ms, take), taken


def code_of(source, **kw):
    with pytest.raises(EntropyError) as e:
        fetch(source, **kw)
    return e.value.code


def test_200_returns_block_and_sends_headers(stub):
    result, taken = fetch(QryptHttpSource(stub.url))
    assert len(result.block) == 1024 and result.http_status == 200 and not taken
    assert stub.requests[0]["path"] == "/api/v1/quantum-entropy?size=1"
    assert stub.requests[0]["auth"] == f"Bearer {TOKEN}"
    assert result.source == "qrypt" and result.endpoint == "127"


@pytest.mark.parametrize(
    ("status", "code", "requests"),
    [
        (400, ErrorCode.QRYPT_BAD_REQUEST, 1),
        (401, ErrorCode.QRYPT_AUTH_FAILED, 1),
        (403, ErrorCode.QRYPT_QUOTA_EXHAUSTED, 1),
        (429, ErrorCode.QRYPT_RATE_LIMITED, 2),
        (500, ErrorCode.QRYPT_SERVER_ERROR, 2),
        (503, ErrorCode.QRYPT_ENTROPY_UNAVAILABLE, 2),
    ],
)
def test_http_errors_map_and_retry_only_retryable(stub, status, code, requests):
    stub.actions = [(status, "{}"), (status, "{}")]
    assert code_of(QryptHttpSource(stub.url)) is code
    assert len(stub.requests) == requests  # never retried: 400, 401, 403


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"random": [base64.b64encode(b"x" * 1000).decode()]}),  # short block
        json.dumps({"random": [base64.b64encode(bytes(1024)).decode()]}),  # all zero
    ],
)
def test_bad_bodies_are_bad_response_not_retried(stub, body):
    stub.actions = [(200, body)]
    assert code_of(QryptHttpSource(stub.url)) is ErrorCode.QRYPT_BAD_RESPONSE
    assert len(stub.requests) == 1


def test_one_retry_then_success_takes_a_second_slot(stub):
    stub.actions = [(500, "{}")]
    result, taken = fetch(QryptHttpSource(stub.url))
    assert len(result.block) == 1024 and len(taken) == 1 and len(stub.requests) == 2


def test_refused_second_slot_returns_original_qrypt_error(stub):
    stub.actions = [(429, "{}")]
    assert code_of(QryptHttpSource(stub.url), slots=False) is ErrorCode.QRYPT_RATE_LIMITED
    assert len(stub.requests) == 1


def test_deadline_is_one_budget(stub):
    stub.actions = [("sleep", 0.6), ("sleep", 0.6)]
    t0 = time.monotonic()
    assert code_of(QryptHttpSource(stub.url), budget_ms=200) is ErrorCode.QRYPT_TIMEOUT
    assert time.monotonic() - t0 < 0.8  # 200 ms budget + jitter pause, never a second full wait


def test_connection_refused_is_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listens
    source = QryptHttpSource(f"http://127.0.0.1:{port}/q")
    assert code_of(source) is ErrorCode.QRYPT_UNREACHABLE


def test_idle_keepalive_closed_is_reopened_without_counting_as_retry(stub):
    source = QryptHttpSource(stub.url)
    stub.actions = [("drop_after", (200, GOOD))]
    fetch(source)
    time.sleep(0.05)  # the server has closed the idle connection
    result, taken = fetch(source)
    assert result.http_status == 200 and not taken  # reconnect is not the retry: no slot


def test_every_attempt_logs_qrypt_call_and_latency(capsys):
    fake = FakeEntropySource(failures=[ErrorCode.QRYPT_SERVER_ERROR])
    fetch(fake)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    calls = [entry for entry in lines if entry.get("event") == "qrypt_call"]
    assert [(c["attempt"], c["outcome"], c["http_status"]) for c in calls] == [
        (1, "QRYPT_SERVER_ERROR", 500),
        (2, "ok", 200),
    ]
    assert sum("QryptLatencyMs" in entry for entry in lines) == 2


def test_transport_error_logs_null_status(capsys):
    fake = FakeEntropySource(failures=[ErrorCode.QRYPT_UNREACHABLE, ErrorCode.QRYPT_UNREACHABLE])
    with pytest.raises(EntropyError):
        fetch(fake)
    calls = [json.loads(x) for x in capsys.readouterr().out.splitlines() if "qrypt_call" in x]
    assert [c["http_status"] for c in calls] == [None, None]


def test_token_never_logged(stub, capsys):
    stub.actions = [(401, "{}")]
    with pytest.raises(EntropyError):
        fetch(QryptHttpSource(stub.url))
    fetch(QryptHttpSource(stub.url))
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err


def test_fake_is_deterministic_and_counts():
    a, b = FakeEntropySource(seed=b"s"), FakeEntropySource(seed=b"s")
    first = fetch(a)[0].block
    assert first == fetch(b)[0].block  # same seed, same blocks (the reused-block test)
    assert fetch(a)[0].block != first  # the counter advances
    assert a.calls == 2


def test_secret_is_cached(qrypt_param, iam_guard):
    from qmc_api import aws, secret

    aws.reset()
    secret.clear()
    with iam_guard("api") as calls:
        assert secret.get_token() == "dummy-not-a-token"
        assert secret.get_token() == "dummy-not-a-token"
    assert calls.count(("ssm", "GetParameter")) == 1
    secret.clear()


def test_missing_secret_is_secret_unavailable(moto_aws):
    from qmc_api import aws, secret
    from qmc_core.errors import QmcError

    aws.reset()
    secret.clear()
    with pytest.raises(QmcError) as e:
        secret.get_token()
    assert e.value.code is ErrorCode.SECRET_UNAVAILABLE
