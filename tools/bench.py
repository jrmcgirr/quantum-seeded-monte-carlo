"""make bench (QMC-147): the assignment's Performance Benchmarks on the deployed stack.

- Submit latency: 3 warm-ups + N (50) small submits, sequential, paced 2.5 s start-to-start
  (the limiter keeps Qrypt calls ≥ 2 s apart, contract change 006; 0.5 s of margin keeps latency
  jitter from bringing two calls closer than the gap); client-side and REPORT ``Duration``; any 429
  counted, never dropped.
- Cold starts: every REPORT line with ``Init Duration`` in the API and worker log groups
  (natural cold starts only; no configuration change). ``--cold`` times one request; run it
  after ≥ 15 min idle.
- Wall clock: R (5) 1M-path / 10-shard jobs, submit → COMPLETE, one at a time.
Qrypt: 53 KiB for submits + 5 for wall clock (+1 for --cold). Output is Markdown for BENCHMARKS.md.
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
import time
from collections import Counter

from _common import REFERENCE, call, log_events, log_group, wait

SMALL = {**REFERENCE, "paths": 1000, "shards": 1}
REPORT = re.compile(
    r"RequestId: (\S+)\s+Duration: ([\d.]+) ms.*?(?:Init Duration: ([\d.]+) ms)?\s*$"
)


def pct(xs: list[float], p: float) -> float:
    """Nearest-rank percentile (no interpolation): the value a reader can find in the raw list."""
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def reports(fn: str, since: float) -> dict[str, tuple[float, float | None]]:
    out = {}
    for e in log_events(log_group(fn), '"REPORT RequestId"', since):
        if m := REPORT.search(e["message"]):
            out[m[1]] = (float(m[2]), float(m[3]) if m[3] else None)
    return out


def submits(n: int, pace_s: float = 2.5) -> None:
    t_start = time.time()
    rows = []
    for k in range(3 + n):
        t0 = time.monotonic()
        status, headers, body = call("POST", "/jobs", SMALL)
        ms = (time.monotonic() - t0) * 1000
        rid = next((v for h, v in headers.items() if h.lower() == "x-request-id"), None)
        if k >= 3:
            rows.append((status, ms, rid, (body.get("error") or {}).get("code")))
        time.sleep(max(0.0, pace_s - (time.monotonic() - t0)))
    time.sleep(15)  # REPORT lines reach CloudWatch Logs within seconds
    rep = reports("api", t_start - 5)
    ok = [r for r in rows if r[0] == 202]
    client = [ms for _, ms, _, _ in ok]
    server = [rep[rid][0] for _, _, rid, _ in ok if rid in rep]
    failed = Counter(f"{r[0]} {r[3]}" for r in rows if r[0] != 202)
    print(
        f"\n## Submit latency (warm), {n} submits after 3 warm-ups, paced {pace_s * 1000:.0f} ms\n"
    )
    print(
        f"accepted (202): {len(ok)} of {n}; **excluded from p50/p95:** {dict(failed) or 'none'} "
        "(a Qrypt 429 → 503 QRYPT_RATE_LIMITED is counted here, never in the latency sample)\n"
    )
    print(
        "| measure | n | p50 ms | p95 ms | max ms | target |\n| --- | --- | --- | --- | --- | --- |"
    )
    for name, xs in (
        ("client round trip (SigV4, laptop → Function URL)", client),
        ("Lambda REPORT Duration", server),
    ):
        if xs:
            print(
                f"| {name} | {len(xs)} | {pct(xs, 50):.0f} | {pct(xs, 95):.0f} | {max(xs):.0f} "
                f"| p95 < 800 |"
            )
    print("\nraw client ms:", ", ".join(f"{x:.0f}" for x in client))


def cold_starts(days: float) -> None:
    since = time.time() - days * 86400
    print(f"\n## Natural cold starts (REPORT lines with Init Duration, last {days:g} days)\n")
    print(
        "| function | cold starts | init p50 ms | init max ms | duration p50 ms |\n| --- | --- | --- | --- | --- |"
    )
    for fn in ("api", "worker"):
        cold = [v for v in reports(fn, since).values() if v[1] is not None]
        if cold:
            inits, durs = [c[1] for c in cold], [c[0] for c in cold]
            print(
                f"| {fn} | {len(cold)} | {statistics.median(inits):.0f} | {max(inits):.0f} "
                f"| {statistics.median(durs):.0f} |"
            )
        else:
            print(f"| {fn} | 0 | | | |")


def cold_once() -> None:
    t0 = time.monotonic()
    status, _, _ = call("POST", "/jobs", SMALL)
    print(
        f"\n## First request after idle: POST /jobs {status} in {(time.monotonic() - t0) * 1000:.0f} ms "
        "(client); its Init Duration appears in the cold-start table"
    )


def wall_clock(runs: int) -> None:
    print(f"\n## 1M paths, 10 shards: submit → COMPLETE, {runs} runs\n")
    print("| run | job | seconds | status | target |\n| --- | --- | --- | --- | --- |")
    times = []
    for k in range(runs):
        t0 = time.monotonic()
        status, _, body = call("POST", "/jobs", {**REFERENCE, "paths": 1_000_000, "shards": 10})
        if status != 202:
            print(f"| {k + 1} | submit {status} {body} | | | |")
            continue
        view, _ = wait(body["job_id"], timeout_s=300)
        times.append(time.monotonic() - t0)
        print(
            f"| {k + 1} | {body['job_id']} | {times[-1]:.1f} | {view['status']} | < 60 |",
            flush=True,
        )
    if times:
        print(f"\nmedian {statistics.median(times):.1f} s, max {max(times):.1f} s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submits", type=int, default=50)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument(
        "--cold", action="store_true", help="time one request first (after ≥ 15 min idle)"
    )
    ap.add_argument("--days", type=float, default=2, help="cold-start look-back")
    args = ap.parse_args()
    assert args.submits <= 50 and args.runs <= 5, "Qrypt quota: at most 50 submits and 5 runs"
    if args.cold:
        cold_once()
    if args.submits:
        submits(args.submits)
    if args.runs:
        wall_clock(args.runs)
    cold_starts(args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
