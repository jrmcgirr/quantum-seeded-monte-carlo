"""make burst (QMC-143): 40 concurrent submits must never put more than CAP Qrypt calls in one
WINDOW_S limiter window (contract change 005: 1 call per 1 s window), Qrypt must never answer 429,
the overflow must be clean 429 ENTROPY_RATE_LIMITED, and never more than 30 calls in any rolling
10 s (the assignment's limit). Spends one KiB per 202 (about 1–4 at cap 1).

Evidence, three ways: the RL#qrypt limiter items (slots per window, exact), the API's
``qrypt_call`` log lines (per-call timestamps), and the QryptLatencyMs SampleCount.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from _common import ENV, Checks, aws, call, log_events, log_group, outputs, poll

JOB = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0, "paths": 1000, "shards": 1}


def sample_check(t0: float, t1: float):
    """(metric SampleCount, qrypt_call lines) over [minute(t0), minute(t1) + 60); None until they
    agree or the metric has caught up with the log lines."""
    lo, hi = int(t0 // 60) * 60, int(t1 // 60) * 60 + 60
    pts = aws("cloudwatch").get_metric_statistics(
        Namespace=f"QMC/{ENV}",
        MetricName="QryptLatencyMs",
        StartTime=lo,
        EndTime=hi,
        Period=60,
        Statistics=["SampleCount"],
    )["Datapoints"]
    n_metric = int(sum(p["SampleCount"] for p in pts))
    n_logs = len(log_events(log_group("api"), '{ $.event = "qrypt_call" }', lo, hi - 0.001))
    return (n_metric, n_logs) if n_metric >= n_logs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--cap", type=int, default=1, help="QRYPT_MAX_PER_WINDOW (contract change 005)")
    ap.add_argument("--window-s", type=int, default=1, help="QRYPT_WINDOW_S (contract change 005)")
    args = ap.parse_args()
    assert 1 <= args.n <= 40, "burst is capped at 40 submits (Qrypt quota)"
    check = Checks()

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.n) as pool:
        results = list(pool.map(lambda _: call("POST", "/jobs", JOB), range(args.n)))
    t1 = time.time()
    codes = Counter(s for s, _, _ in results)
    errors = Counter(b.get("error", {}).get("code") for s, _, b in results if s != 202)
    print(f"{args.n} submits in {t1 - t0:.2f} s: {dict(codes)} {dict(errors)}")
    check(
        "only 202 or 429 ENTROPY_RATE_LIMITED",
        set(codes) <= {202, 429} and set(errors) <= {"ENTROPY_RATE_LIMITED"},
        dict(errors),
    )
    retry_after = Counter(
        next((v for k, v in h.items() if k.lower() == "retry-after"), None)
        for s, h, _ in results
        if s == 429
    )
    check("every 429 has Retry-After", None not in retry_after, dict(retry_after))

    # Limiter windows (exact slot counts, written by the API).
    w_s = args.window_s
    table = outputs()["table_name"]
    windows = range(int(t0 // w_s) * w_s, int(t1 // w_s) * w_s + w_s, w_s)
    slots = {}
    for w in windows:
        item = (
            aws("dynamodb")
            .get_item(
                TableName=table,
                ConsistentRead=True,
                Key={"pk": {"S": "RL#qrypt"}, "sk": {"S": str(w)}},
            )
            .get("Item")
        )
        slots[w] = int(item["n"]["N"]) if item else 0
    check(
        f"limiter: ≤ {args.cap} slots per {w_s} s window",
        max(slots.values()) <= args.cap,
        {w: n for w, n in slots.items() if n},
    )

    # qrypt_call log lines: one per HTTP attempt (HANDOFF §9.3).
    pattern = '{ $.event = "qrypt_call" }'
    lines, _ = poll(
        "qrypt_call lines",
        lambda: (
            ev
            if len(ev := log_events(log_group("api"), pattern, t0 - 1, t1 + 30)) >= codes[202]
            else None
        ),
        180,
        10,
    )
    ts = sorted(e["timestamp"] / 1000 for e in lines)
    per_window = Counter(int(t // w_s) * w_s for t in ts)
    statuses = Counter(json.loads(e["message"]).get("http_status") for e in lines)
    rolling = max(sum(1 for u in ts if t <= u < t + 10) for t in ts)
    check(
        "qrypt_call lines ≥ accepted jobs",
        len(ts) >= codes[202],
        f"{len(ts)} calls, {codes[202]} jobs",
    )
    # Guards: the slots read must cover the calls seen (a changed RL key format would otherwise
    # make the slot check pass on zeros), and Qrypt itself must never have said 429.
    check(
        "limiter slots ≥ Qrypt calls",
        sum(slots.values()) >= len(ts),
        f"{sum(slots.values())} vs {len(ts)}",
    )
    check("no Qrypt 429", not statuses.get(429), dict(statuses))
    # Fixed windows: a rolling 10 s interval can touch ceil(10 / w) + 1 windows at its edges.
    bound = args.cap * (math.ceil(10 / w_s) + 1)
    check(f"≤ {bound} Qrypt calls in any rolling 10 s", rolling <= bound, f"max {rolling}")
    check("≤ 30 Qrypt calls in any rolling 10 s (assignment)", rolling <= 30, f"max {rolling}")
    # Informational: a log line is stamped after its limiter slot, so a call near a boundary can
    # land in the next window here. The exact per-window count is the RL#qrypt check above.
    print(f"qrypt_call per fixed window (log timestamps): {dict(sorted(per_window.items()))}")

    # QryptLatencyMs SampleCount vs qrypt_call lines over the same whole-minute buckets
    # (EMF; minutes of lag possible).
    try:
        (n_metric, n_logs), secs = poll(
            "QryptLatencyMs SampleCount", lambda: sample_check(t0, t1), 600, 30
        )
        check(
            "QryptLatencyMs SampleCount = qrypt_call lines (same minutes)",
            n_metric == n_logs,
            f"{n_metric} vs {n_logs} (after {secs:.0f} s)",
        )
    except TimeoutError as e:
        check("QryptLatencyMs SampleCount = qrypt_call lines (same minutes)", False, e)

    print(f"Qrypt spent: {len(ts)} KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
