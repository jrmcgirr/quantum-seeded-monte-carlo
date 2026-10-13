"""Shared by the WS4 tools: signed calls through clients/qmc.py, 2 s polling, a check table."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.qmc import request  # WS2 contract: (status, headers, json_body), any status

REFERENCE = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}
TERMINAL = {"COMPLETE", "PARTIAL", "FAILED"}
POLL_S = 2.0  # never faster: 10 RCU, consistent reads


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
    return request(method, path, body)


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
