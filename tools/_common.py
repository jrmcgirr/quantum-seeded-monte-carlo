"""Shared by the WS4 tools: signed calls through clients/qmc.py, 2 s polling, a check table."""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from clients.qmc import request  # noqa: E402 (WS2: status, headers, json; any status)

REFERENCE = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}
TERMINAL = {"COMPLETE", "PARTIAL", "FAILED"}
POLL_S = 2.0  # never faster: 10 RCU, consistent reads


def api_url() -> str:
    return os.environ.get("QMC_API_URL") or outputs()["api_url"]


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
    return request(method, path, body, url=api_url())


def wait(job_id: str, timeout_s: float = 180.0, on_view=None) -> tuple[dict, float]:
    """Poll GET /jobs/{id} every 2 s until terminal. Returns (view, seconds since the call)."""
    t0 = time.monotonic()
    while True:
        status, _, view = call("GET", f"/jobs/{job_id}")
        if status != 200:
            raise RuntimeError(f"GET /jobs/{job_id} → {status} {view}")
        if on_view:
            on_view(view, time.monotonic() - t0)
        if view["status"] in TERMINAL:
            return view, time.monotonic() - t0
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"{job_id} still {view['status']} after {timeout_s:.0f} s")
        time.sleep(POLL_S)


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def __call__(self, name: str, ok: bool, detail: object = "") -> bool:
        self.rows.append((name, bool(ok), str(detail)))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)
        return bool(ok)

    def summary(self) -> int:
        w = max(len(n) for n, _, _ in self.rows)
        print("\n| check | result | detail |\n| --- | --- | --- |")
        for name, ok, detail in self.rows:
            print(f"| {name.ljust(w)} | {'PASS' if ok else 'FAIL'} | {detail} |")
        failed = sum(not ok for _, ok, _ in self.rows)
        print(f"\n{len(self.rows) - failed}/{len(self.rows)} passed")
        return 1 if failed else 0


# --- deployed stack (read-only; tools never apply) ---------------------------------------------
ENV = os.environ.get("ENV", "dev")
REGION = os.environ.get("AWS_REGION", "us-east-2")


_outputs_lock = threading.Lock()


def outputs() -> dict:
    """``make outputs`` as {name: value}; thread-safe (burst and replay_many call it from threads)."""
    with _outputs_lock:
        return _outputs()


@functools.cache
def _outputs() -> dict:
    out = subprocess.run(
        ["make", "-s", "outputs", f"ENV={ENV}"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    return {k: v["value"] for k, v in json.loads(out).items()}


@functools.cache
def aws(service: str):
    import boto3

    return boto3.client(service, region_name=REGION)


def log_events(group: str, pattern: str, start_s: float, end_s: float | None = None) -> list[dict]:
    """Every matching event in ``group`` between two epoch times (paginated)."""
    kw = {"logGroupName": group, "filterPattern": pattern, "startTime": int(start_s * 1000)}
    if end_s:
        kw["endTime"] = int(end_s * 1000)
    pages = aws("logs").get_paginator("filter_log_events").paginate(**kw)
    return [e for page in pages for e in page["events"]]


def log_group(fn: str) -> str:
    return f"/aws/lambda/qmc-{ENV}-{fn}"


def poll(what: str, fn, timeout_s: float, every_s: float):
    """Call ``fn`` until it returns truthy; returns (value, seconds). Raises TimeoutError."""
    t0 = time.monotonic()
    while True:
        value = fn()
        if value:
            return value, time.monotonic() - t0
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"{what}: not after {timeout_s:.0f} s")
        time.sleep(every_s)
