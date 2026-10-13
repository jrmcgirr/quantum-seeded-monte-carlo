"""make burst (QMC-143): 40 concurrent submits against the Qrypt limiter of contract change 006
(HANDOFF 1.13): one item ``RL#qrypt/LAST``, consecutive Qrypt calls at least ``QRYPT_MIN_GAP_MS``
(2,000) apart, so at most ceil(10,000 / gap) = 5 in any rolling 10 s (assignment: 30), no retry
on a Qrypt 429. Overflow must be 429 ENTROPY_RATE_LIMITED with Retry-After. No Qrypt 429 is
expected; any that happens must fail closed (503 QRYPT_RATE_LIMITED, nothing written). About
1–2 KiB per run.

``--recheck START END`` re-runs the server-side checks on an earlier burst today, without
submitting.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import math
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from _common import ENV, Checks, aws, call, log_events, log_group, outputs, poll

from qmc_core.codec import iso_ms

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


def qrypt_checks(check, lines, t0, t1, gap_ms, http_503=None) -> None:
    """Server-side checks (log lines, the LAST item, DynamoDB) shared by a live run and --recheck."""
    calls = sorted((json.loads(e["message"]) for e in lines), key=lambda c: c["ts"])
    # A call's start is its log time minus its latency; the slot is taken a few ms before that.
    starts = [
        dt.datetime.fromisoformat(c["ts"].replace("Z", "+00:00")).timestamp()
        - c["latency_ms"] / 1000
        for c in calls
    ]
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    tol = 0.25  # slot-to-request delay: a few ms, up to ~100 ms on an SSM cache miss; stated, not tuned
    check(
        f"Qrypt calls ≥ {gap_ms / 1000:g} s apart (start to start, −{tol:g} s tolerance)",
        all(g >= gap_ms / 1000 - tol for g in gaps),
        f"{len(calls)} calls; gaps {[round(g, 3) for g in gaps]}",
    )
    bound = math.ceil(10_000 / gap_ms)
    rolling = max((sum(1 for u in starts if t <= u < t + 10) for t in starts), default=0)
    check(f"≤ {bound} Qrypt calls in any rolling 10 s", rolling <= bound, f"max {rolling}")
    check("≤ 30 Qrypt calls in any rolling 10 s (assignment)", rolling <= 30, f"max {rolling}")

    # Guard: the LAST item must be the last call's slot (a changed key or attribute would
    # otherwise leave the gap unchecked by the table).
    item = (
        aws("dynamodb")
        .get_item(
            TableName=outputs()["table_name"],
            ConsistentRead=True,
            Key={"pk": {"S": "RL#qrypt"}, "sk": {"S": "LAST"}},
        )
        .get("Item")
    )
    at = int(item["at_ms"]["N"]) / 1000 if item and "at_ms" in item else None
    check(
        "RL#qrypt/LAST at_ms = last call's slot (within 1 s before its start)",
        at is not None and bool(starts) and starts[-1] - 1 <= at <= starts[-1] + 0.05,
        f"at_ms {at} vs last start {starts[-1] if starts else None}",
    )

    statuses = Counter(c["http_status"] for c in calls)
    n429 = statuses.get(429, 0)
    check("no Qrypt 429 (expected under 006)", n429 == 0, dict(statuses))
    failed = [
        json.loads(e["message"])
        for e in log_events(
            log_group("api"),
            '{ $.event = "seeding_failed" && $.cause = "QRYPT_RATE_LIMITED" }',
            t0 - 1,
            t1 + 30,
        )
    ]
    ids = {f["job_id"] for f in failed}
    detail = f"{len(ids)} seeding_failed QRYPT_RATE_LIMITED" + (
        f", {http_503} HTTP 503" if http_503 is not None else ""
    )
    check(
        "every Qrypt 429 → one 503 QRYPT_RATE_LIMITED submit",
        len(ids) == n429 and http_503 in (None, n429),
        detail,
    )
    table = outputs()["table_name"]
    ddb = aws("dynamodb")
    jobs = [
        i
        for i in ids
        if "Item"
        in ddb.get_item(
            TableName=table, ConsistentRead=True, Key={"pk": {"S": f"JOB#{i}"}, "sk": {"S": "META"}}
        )
    ]
    listed = {
        x["job_id"]["S"]
        for page in ddb.get_paginator("query").paginate(
            TableName=table,
            ConsistentRead=True,
            ProjectionExpression="job_id",
            KeyConditionExpression="pk = :p AND sk BETWEEN :a AND :b",
            ExpressionAttributeValues={
                ":p": {"S": "JOBLIST"},
                ":a": {"S": iso_ms(t0 - 5)},
                ":b": {"S": iso_ms(t1 + 60)},
            },
        )
        for x in page["Items"]
    }
    entropy = [
        x
        for page in ddb.get_paginator("scan").paginate(
            TableName=table,
            ProjectionExpression="pk, job_id",
            FilterExpression="begins_with(pk, :e)",
            ExpressionAttributeValues={":e": {"S": "ENTROPY#"}},
        )
        for x in page["Items"]
        if x.get("job_id", {}).get("S") in ids
    ]
    check(
        "nothing written for those submits (job, JOBLIST, entropy)",
        not jobs and not (ids & listed) and not entropy,
        f"job items {len(jobs)}, JOBLIST {len(ids & listed)}, entropy {len(entropy)}",
    )


def recheck(start: str, end: str, gap_ms: int) -> int:
    """Server-side checks on a past burst today (UTC ``HH:MM:SSZ``); no submits."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    t0, t1 = (
        dt.datetime.fromisoformat(f"{day}T{x.rstrip('Z')}+00:00").timestamp() for x in (start, end)
    )
    check = Checks()
    lines = log_events(log_group("api"), '{ $.event = "qrypt_call" }', t0 - 1, t1 + 30)
    print(f"recheck {start}–{end}: {len(lines)} qrypt_call lines")
    qrypt_checks(check, lines, t0, t1, gap_ms)
    return check.summary()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument(
        "--gap-ms", type=int, default=2000, help="QRYPT_MIN_GAP_MS (contract change 006)"
    )
    ap.add_argument("--recheck", nargs=2, metavar=("START", "END"))
    args = ap.parse_args()
    if args.recheck:
        return recheck(*args.recheck, args.gap_ms)
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
        "only 202, 429 ENTROPY_RATE_LIMITED or 503 QRYPT_RATE_LIMITED",
        all(
            s == 202
            or (b.get("error") or {}).get("code")
            == ("ENTROPY_RATE_LIMITED" if s == 429 else "QRYPT_RATE_LIMITED" if s == 503 else None)
            for s, _, b in results
        ),
        dict(errors),
    )
    retry_after = Counter(
        next((v for k, v in h.items() if k.lower() == "retry-after"), None)
        for s, h, _ in results
        if s == 429
    )
    check("every 429 has Retry-After", None not in retry_after, dict(retry_after))

    # One qrypt_call line per HTTP attempt (HANDOFF §9.3). Every 202 and every 503 QRYPT_* reached
    # Qrypt, so wait for at least that many lines, then 30 s more (an early stop once hid a 429).
    reached = codes[202] + sum(n for c, n in errors.items() if (c or "").startswith("QRYPT_"))
    pattern = '{ $.event = "qrypt_call" }'
    poll(
        "qrypt_call lines",
        lambda: len(log_events(log_group("api"), pattern, t0 - 1, t1 + 30)) >= reached,
        180,
        10,
    )
    time.sleep(30)
    lines = log_events(log_group("api"), pattern, t0 - 1, t1 + 30)
    check(
        "qrypt_call lines ≥ submits that reached Qrypt",
        len(lines) >= reached,
        f"{len(lines)} calls, {reached} submits reached Qrypt ({codes[202]} accepted)",
    )
    qrypt_checks(check, lines, t0, t1, args.gap_ms, http_503=errors["QRYPT_RATE_LIMITED"])

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

    print(f"Qrypt spent: {codes[202]} KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
