"""QMC-128 deployed drill (contract change 008): N (10) concurrent opt-in submits with
``on_entropy_failure: "queue"``. The first takes the Qrypt gap slot; the rest hit our limiter
(retryable) and must come back 202 PENDING_ENTROPY, then the async seeder must seed each from Qrypt
(never local) and every job must reach COMPLETE. Also checks: Qrypt calls ≥ 2 s apart and no
Qrypt 429 (burst.qrypt_checks), no seeder errors in the API log, one replay bit-identical.
Polls one job at a time at 2 s. Qrypt: ≤ N KiB, one per Qrypt call (the replay reuses the stored seed).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from _common import Checks, call, log_events, log_group, wait
from burst import JOB, qrypt_checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="≤ MAX_PENDING_ENTROPY (10)")
    ap.add_argument("--gap-ms", type=int, default=2000)
    args = ap.parse_args()
    assert 1 <= args.n <= 10, "capped at 10 submits (MAX_PENDING_ENTROPY, Qrypt quota)"
    check = Checks()
    body = {**JOB, "on_entropy_failure": "queue"}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.n) as pool:
        results = list(pool.map(lambda _: call("POST", "/jobs", body), range(args.n)))
    first = Counter(
        f"{s} {b.get('status') or (b.get('error') or {}).get('code')}" for s, _, b in results
    )
    check(
        "every submit 202 (QUEUED or PENDING_ENTROPY)",
        all(s == 202 for s, _, _ in results),
        dict(first),
    )
    check(
        "some submits queued as PENDING_ENTROPY",
        any(b.get("status") == "PENDING_ENTROPY" for _, _, b in results),
        dict(first),
    )

    ids = [b["job_id"] for s, _, b in results if s == 202]
    views = {}
    for job_id in ids:  # one GET every 2 s; later jobs keep seeding meanwhile
        views[job_id], _ = wait(job_id, timeout_s=300)
    t1 = time.time()
    final = Counter(v["status"] for v in views.values())
    check(f"all {len(ids)} jobs COMPLETE", final == Counter({"COMPLETE": args.n}), dict(final))
    for v in views.values():
        if v["status"] != "COMPLETE":
            print(
                f"  {v['job_id']}: {v['status']} {v.get('fail_reason')} {v.get('last_entropy_error')}"
            )

    time.sleep(30)  # log lines reach CloudWatch Logs within seconds
    api = log_group("api")
    lines = log_events(api, '{ $.event = "qrypt_call" }', t0 - 1, t1 + 30)
    check(
        "one qrypt_call per job at least",
        len(lines) >= args.n,
        f"{len(lines)} calls for {args.n} jobs",
    )
    qrypt_checks(check, lines, t0, t1, args.gap_ms, http_503=0)
    events = Counter(
        json.loads(e["message"])["event"]
        for e in log_events(
            api,
            '{ $.event = "entropy_seeded" || $.event = "duplicate_seeder" || $.event = "seeder_error" || $.event = "seeding_failed" || $.event = "seeder_invoke_failed" }',
            t0 - 1,
            t1 + 30,
        )
    )
    check(
        "seeder log: entropy_seeded only (no duplicate_seeder, seeder_error, seeding_failed, seeder_invoke_failed)",
        set(events) <= {"entropy_seeded"},
        dict(events),
    )

    pending = [
        b["job_id"]
        for _, _, b in results
        if b.get("status") == "PENDING_ENTROPY" and views[b["job_id"]]["status"] == "COMPLETE"
    ]
    check("a queued job completed (to replay)", bool(pending), len(pending))
    if pending:
        s, _, r = call("POST", f"/jobs/{pending[0]}/replay")
        rv, _ = wait(r["job_id"]) if s == 202 else ({}, 0)
        rc = rv.get("replay_check") or {}
        check(
            "replay of a queued job bit-identical",
            rc.get("bit_identical") is True,
            f"{pending[0]} → {r.get('job_id')} {rc}",
        )

    print(f"jobs: {' '.join(ids)}\nQrypt spent: {len(lines)} KiB (one per qrypt_call)")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
