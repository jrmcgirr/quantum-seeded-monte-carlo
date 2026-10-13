"""make replay-check N=20 (QMC-146): replay one COMPLETE 1M-path job N times, in batches of 5
(concurrent within a batch, ≥ 10 min apart so execution environments are recycled). No Qrypt.

Reports bit_identical for each replay, the distinct CPU models and the worker log streams
(execution environments) that ran the shards. No configuration change forces cold starts.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from _common import Checks, aws, call, log_events, log_group, outputs, wait


def cpu_models(job_id: str) -> set[str]:
    """From the shard items (the job view leaves ``cpu_model`` out); operator credentials."""
    pages = (
        aws("dynamodb")
        .get_paginator("query")
        .paginate(
            TableName=outputs()["table_name"],
            ConsistentRead=True,
            KeyConditionExpression="pk = :p AND begins_with(sk, :s)",
            ExpressionAttributeValues={":p": {"S": f"JOB#{job_id}"}, ":s": {"S": "SHARD#"}},
            ProjectionExpression="fingerprint.cpu_model",
        )
    )
    return {i["fingerprint"]["M"]["cpu_model"]["S"] for page in pages for i in page["Items"]}


def reference_job() -> str:
    _, _, body = call("GET", "/jobs")
    for j in body["jobs"]:
        if j["status"] == "COMPLETE" and j["paths"] == 1_000_000 and not j.get("replay_of"):
            return j["job_id"]
    sys.exit("no COMPLETE 1M-path original job; run make e2e first or pass --job")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--gap-min", type=float, default=10.0)
    ap.add_argument("--job", help="original job id (default: newest COMPLETE 1M-path job)")
    args = ap.parse_args()
    original = args.job or reference_job()
    check = Checks()
    print(
        f"original {original}: {args.n} replays, batches of {args.batch}, {args.gap_min} min apart"
    )

    rows = []
    t_start = time.time()
    for b, start in enumerate(range(0, args.n, args.batch)):
        if b:
            print(f"sleeping {args.gap_min} min (execution environments recycle)", flush=True)
            time.sleep(args.gap_min * 60)
        size = min(args.batch, args.n - start)
        print(f"batch {b + 1} starts {time.strftime('%H:%M:%SZ', time.gmtime())}", flush=True)
        posted = [call("POST", f"/jobs/{original}/replay") for _ in range(size)]
        ids = [body["job_id"] for status, _, body in posted if status == 202]
        check(
            f"batch {b + 1}: {size} replays accepted", len(ids) == size, [s for s, _, _ in posted]
        )
        with ThreadPoolExecutor(max_workers=size) as pool:
            views = list(pool.map(lambda i: wait(i, timeout_s=300)[0], ids))
        for v in views:
            rc = v.get("replay_check") or {}
            rows.append(
                (b + 1, v["job_id"], v["status"], rc.get("bit_identical"), cpu_models(v["job_id"]))
            )
            check(
                f"replay {v['job_id'][:8]} bit-identical",
                rc.get("bit_identical") is True,
                f"batch {b + 1} fp_match={rc.get('fingerprint_match')} diffs={rc.get('shard_diffs')}",
            )

    streams: set[str] = set()
    for _, job_id, *_ in rows:
        streams |= {
            e["logStreamName"]
            for e in log_events(log_group("worker"), f'{{ $.job_id = "{job_id}" }}', t_start - 60)
        }
    cpus = set().union(*(r[4] for r in rows)) if rows else set()
    print(f"\ndistinct CPU models ({len(cpus)}):", *sorted(cpus), sep="\n  ")
    print(
        f"distinct worker execution environments (log streams): {len(streams)}",
        *sorted(streams),
        sep="\n  ",
    )
    check(
        f"all {args.n} bit-identical",
        len(rows) == args.n and all(r[3] is True for r in rows),
        f"{sum(r[3] is True for r in rows)}/{args.n}",
    )
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
