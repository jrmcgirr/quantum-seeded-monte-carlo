"""make burst (QMC-143): 40 concurrent submits must never put more than 15 Qrypt calls in one
limiter window (HANDOFF D10), so never more than 30 in any rolling 10 s (the assignment's limit).
Spends at most 40 KiB of Qrypt (one per 202, plus any retry).

Evidence, three ways: the RL#qrypt limiter items (slots per window, exact), the API's
``qrypt_call`` log lines (per-call timestamps), and the QryptLatencyMs SampleCount.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from _common import ENV, Checks, aws, call, log_events, log_group, outputs, poll

JOB = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0, "paths": 1000, "shards": 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
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
    check(
        "every 429 has Retry-After",
        all(any(k.lower() == "retry-after" for k in h) for s, h, _ in results if s == 429),
    )

    # Limiter windows (exact slot counts, written by the API).
    table, windows = outputs()["table_name"], range(int(t0 // 10) * 10, int(t1 // 10) * 10 + 10, 10)
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
    check("limiter: ≤ 15 slots per 10 s window", max(slots.values()) <= 15, slots)

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
    per_window = Counter(int(t // 10) * 10 for t in ts)
    rolling = max(sum(1 for u in ts if t <= u < t + 10) for t in ts)
    check(
        "qrypt_call lines ≥ accepted jobs",
        len(ts) >= codes[202],
        f"{len(ts)} calls, {codes[202]} jobs",
    )
    check("≤ 30 Qrypt calls in any rolling 10 s", rolling <= 30, f"max {rolling}")
    print(f"qrypt_call per fixed window (log timestamps): {dict(sorted(per_window.items()))}")

    # QryptLatencyMs SampleCount (EMF; minutes of lag possible).
    def samples():
        pts = aws("cloudwatch").get_metric_statistics(
            Namespace=f"QMC/{ENV}",
            MetricName="QryptLatencyMs",
            StartTime=t0 - 60,
            EndTime=t1 + 60,
            Period=60,
            Statistics=["SampleCount"],
        )["Datapoints"]
        n = int(sum(p["SampleCount"] for p in pts))
        return n if n >= len(ts) else None

    try:
        n, secs = poll("QryptLatencyMs SampleCount", samples, 600, 30)
        check(
            "QryptLatencyMs SampleCount = qrypt_call lines",
            n == len(ts),
            f"{n} (after {secs:.0f} s)",
        )
    except TimeoutError as e:
        check("QryptLatencyMs SampleCount = qrypt_call lines", False, e)

    print(f"Qrypt spent: {len(ts)} KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
