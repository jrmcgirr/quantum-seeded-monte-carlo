#!/usr/bin/env python3
"""SigV4 client for the QMC Function URL (HANDOFF §5.5). Standard library plus botocore.

    python clients/qmc.py submit --S0 100 --K 100 --r 0.05 --sigma 0.2 --T 1 --paths 1000000 --shards 10
    python clients/qmc.py wait <job_id>
    python clients/qmc.py replay <job_id>
    python clients/qmc.py get <job_id> | list | health

Credentials come from the normal AWS chain (profile, environment, the grader user's keys).
The URL comes from --url, else $QMC_API_URL, else ``terraform -chdir=infra output -raw api_url``.
Prints the JSON response; exits non-zero on HTTP >= 400 (``wait``: unless the job is COMPLETE).

Importable: ``request(method, path, body=None, url=None) -> (status, headers, body)``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import MissingDependencyException
from botocore.session import Session

POLL_S = 2.0  # 10 RCU: never poll faster
TERMINAL = ("COMPLETE", "PARTIAL", "FAILED")
INFRA_DIR = Path(__file__).resolve().parent.parent / "infra"


def resolve_url(url: str | None = None) -> str:
    url = url or os.environ.get("QMC_API_URL")
    if not url:
        try:
            url = subprocess.run(
                ["terraform", f"-chdir={INFRA_DIR}", "output", "-raw", "api_url"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            url = ""
    if not url:  # also a checkout with no deployed stack: terraform output is empty
        print("no API URL: run make apply, or pass --url / set QMC_API_URL", file=sys.stderr)
        raise SystemExit(2)
    return url.rstrip("/")


def _region(url: str) -> str:
    # <id>.lambda-url.<region>.on.aws
    parts = urlsplit(url).hostname.split(".")
    return parts[parts.index("lambda-url") + 1]


def _credentials():
    try:
        creds = Session().get_credentials()
        frozen = creds.get_frozen_credentials() if creds else None
    except MissingDependencyException:
        raise SystemExit(
            "These credentials (aws login) need the AWS CRT: run `uv sync` in this repo, "
            "or `pip install 'botocore[crt]'`."
        ) from None
    if frozen is None:
        raise SystemExit("No AWS credentials found (profile, environment or instance role).")
    return frozen


def request(
    method: str, path: str, body: dict | None = None, url: str | None = None
) -> tuple[int, dict, dict]:
    """One signed call → ``(status, response headers, parsed JSON body)``. Never raises on HTTP
    status; a non-JSON body comes back as ``{"raw": text}``."""
    base = resolve_url(url)
    full = base + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data is not None else {}
    signed = AWSRequest(method=method, url=full, data=data, headers=headers)
    SigV4Auth(_credentials(), "lambda", _region(base)).add_auth(signed)
    req = urllib.request.Request(full, data=data, headers=dict(signed.headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, resp_headers, raw = resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        status, resp_headers, raw = err.code, dict(err.headers), err.read()
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError:
        parsed = {"raw": text}
    return status, resp_headers, parsed


def _print(body: dict) -> None:
    print(json.dumps(body, indent=2))


def _submit_body(args) -> dict:
    if args.file:
        return json.loads(Path(args.file).read_text())
    body = {
        k: getattr(args, k)
        for k in ("S0", "K", "r", "sigma", "T", "paths", "shards")
        if getattr(args, k) is not None
    }
    if args.variance_reduction or args.antithetic:
        body["variance_reduction"] = "antithetic" if args.antithetic else args.variance_reduction
    if args.queue:
        body["on_entropy_failure"] = "queue"
    if args.fail_shards:
        body["fault_injection"] = {"fail_shards": args.fail_shards}
    return body


def wait(job_id: str, url: str | None = None, timeout_s: float = 1200) -> tuple[int, dict]:
    """Poll every 2 s until a terminal status; print each change with the elapsed time (stderr)."""
    t0, last = time.monotonic(), None
    while True:
        status, _, body = request("GET", f"/jobs/{job_id}", url=url)
        if status >= 400:
            return status, body
        p = body.get("progress", {})
        seen = (body.get("status"), p.get("done"), p.get("failed"))
        if seen != last:
            print(
                f"[{time.monotonic() - t0:7.1f}s] {seen[0]} "
                f"done={seen[1]}/{p.get('total')} failed={seen[2]}",
                file=sys.stderr,
                flush=True,
            )
            last = seen
        if body.get("status") in TERMINAL or time.monotonic() - t0 > timeout_s:
            return status, body
        time.sleep(POLL_S)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qmc", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="Function URL (default $QMC_API_URL or terraform output)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="POST /jobs")
    for name in ("S0", "K", "r", "sigma", "T"):
        s.add_argument(f"--{name}", type=float)
    s.add_argument("--paths", type=int)
    s.add_argument("--shards", type=int)
    s.add_argument("--variance-reduction", choices=("none", "antithetic"))
    s.add_argument("--antithetic", action="store_true", help='variance_reduction "antithetic"')
    s.add_argument(
        "--queue",
        action="store_true",
        help='on_entropy_failure "queue": wait as PENDING_ENTROPY on a retryable Qrypt failure',
    )
    s.add_argument("--fail-shards", type=int, nargs="+", help="fault_injection.fail_shards")
    s.add_argument("--file", help="JSON request body (overrides the flags)")
    for name, help_ in (("get", "GET /jobs/{id}"), ("replay", "POST /jobs/{id}/replay")):
        sub.add_parser(name, help=help_).add_argument("job_id")
    w = sub.add_parser("wait", help="poll GET /jobs/{id} every 2 s until terminal")
    w.add_argument("job_id")
    w.add_argument("--timeout", type=float, default=1200)
    sub.add_parser("list", help="GET /jobs")
    sub.add_parser("health", help="GET /health")
    args = ap.parse_args(argv)

    if args.cmd == "wait":
        status, body = wait(args.job_id, args.url, args.timeout)
        _print(body)
        return 0 if status < 400 and body.get("status") == "COMPLETE" else 1
    method, path, body = {
        "submit": ("POST", "/jobs", _submit_body(args) if args.cmd == "submit" else None),
        "get": ("GET", f"/jobs/{getattr(args, 'job_id', '')}", None),
        "replay": ("POST", f"/jobs/{getattr(args, 'job_id', '')}/replay", None),
        "list": ("GET", "/jobs", None),
        "health": ("GET", "/health", None),
    }[args.cmd]
    status, _, resp = request(method, path, body, args.url)
    _print(resp)
    return 0 if status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
