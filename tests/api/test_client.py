"""QMC-125: the SigV4 client's pure parts (the live check is against the deployed stack)."""

from __future__ import annotations

import pytest

from clients import qmc

URL = "https://abc123.lambda-url.us-east-2.on.aws"


def test_region_from_function_url():
    assert qmc._region(URL) == "us-east-2"


def test_url_resolution_prefers_flag_then_env(monkeypatch):
    monkeypatch.setenv("QMC_API_URL", URL + "/")
    assert qmc.resolve_url() == URL
    assert qmc.resolve_url("https://x.lambda-url.eu-west-1.on.aws").endswith("on.aws")


@pytest.mark.parametrize("terraform", ["", OSError("terraform: not found")])
def test_no_deployed_stack_is_a_one_line_exit_2(monkeypatch, capsys, terraform):
    import subprocess

    def output(*a, **k):  # `terraform output -raw api_url` with no stack prints nothing
        if isinstance(terraform, Exception):
            raise terraform
        return subprocess.CompletedProcess(a, 0, stdout=terraform)

    monkeypatch.delenv("QMC_API_URL", raising=False)
    monkeypatch.setattr(qmc.subprocess, "run", output)
    with pytest.raises(SystemExit) as exit_:
        qmc.main(["health"])
    assert exit_.value.code == 2
    assert capsys.readouterr().err == (
        "no API URL: run make apply, or pass --url / set QMC_API_URL\n"
    )


def test_request_is_signed_and_never_raises_on_4xx(monkeypatch, aws_env):
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(dict(req.header_items()), url=req.full_url, method=req.get_method())
        raise qmc.urllib.error.HTTPError(
            req.full_url,
            404,
            "nf",
            {"x-request-id": "r1"},
            __import__("io").BytesIO(b'{"error": {"code": "JOB_NOT_FOUND"}}'),
        )

    monkeypatch.setattr(qmc.urllib.request, "urlopen", fake_urlopen)
    status, _, body = qmc.request("GET", "/jobs/x", url=URL)
    assert (status, body["error"]["code"]) == (404, "JOB_NOT_FOUND")
    assert sent["Authorization"].startswith("AWS4-HMAC-SHA256")
    assert "/us-east-2/lambda/aws4_request" in sent["Authorization"]
    assert sent["url"] == URL + "/jobs/x"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["submit", "--S0", "100", "--paths", "10", "--shards", "2"],
            ("POST", "/jobs", {"S0": 100.0, "paths": 10, "shards": 2}),
        ),
        (
            ["submit", "--paths", "10", "--shards", "2", "--antithetic", "--queue"],
            (
                "POST",
                "/jobs",
                {
                    "paths": 10,
                    "shards": 2,
                    "variance_reduction": "antithetic",
                    "on_entropy_failure": "queue",
                },
            ),
        ),
        (["get", "abc"], ("GET", "/jobs/abc", None)),
        (["replay", "abc"], ("POST", "/jobs/abc/replay", None)),
        (["list"], ("GET", "/jobs", None)),
        (["health"], ("GET", "/health", None)),
    ],
)
def test_commands_map_to_requests(monkeypatch, argv, expected):
    calls = []
    monkeypatch.setattr(
        qmc, "request", lambda m, p, b=None, u=None: calls.append((m, p, b)) or (400, {}, {})
    )
    assert qmc.main(["--url", URL, *argv]) == 1  # HTTP >= 400 → non-zero
    assert calls == [expected]


def test_wait_prints_each_change_until_terminal(monkeypatch, capsys):
    views = iter(
        [
            {"status": "QUEUED", "progress": {"done": 0, "failed": 0, "total": 2}},
            {"status": "QUEUED", "progress": {"done": 0, "failed": 0, "total": 2}},
            {"status": "RUNNING", "progress": {"done": 1, "failed": 0, "total": 2}},
            {"status": "COMPLETE", "progress": {"done": 2, "failed": 0, "total": 2}},
        ]
    )
    monkeypatch.setattr(qmc, "request", lambda *a, **k: (200, {}, next(views)))
    monkeypatch.setattr(qmc.time, "sleep", lambda s: None)
    assert qmc.main(["--url", URL, "wait", "abc"]) == 0
    err = capsys.readouterr().err.splitlines()
    assert [line.split()[2] for line in err] == ["QUEUED", "RUNNING", "COMPLETE"]


def test_missing_crt_is_a_clear_exit(monkeypatch):
    from botocore.exceptions import MissingDependencyException

    def boom(self):
        raise MissingDependencyException(msg="needs crt")

    monkeypatch.setattr(qmc.Session, "get_credentials", boom)
    with pytest.raises(SystemExit, match="botocore\\[crt\\]"):
        qmc.request("GET", "/health", url=URL)
