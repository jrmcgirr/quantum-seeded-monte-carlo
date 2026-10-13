"""QMC-108 / QMC-126 deployed drill: one antithetic 1M-path job (1 KiB of Qrypt), then
- within 3 SE of Black-Scholes, n = pairs, SE compared with a plain 1M job (--plain JOB_ID);
- replay → bit-identical; shard sums written to --dump for tools/shard_crosscheck.py;
- cost_estimate vs the job's own REPORT lines (worker invocations found by job_id → request_id,
  plus the submit's REPORT), within ±10% on Lambda GB-seconds.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from _common import REFERENCE, Checks, call, log_events, log_group, wait

REPORT = re.compile(r"RequestId: (\S+)\s+Duration: ([\d.]+) ms.*?Memory Size: (\d+) MB")


def gb_seconds_from_reports(fn: str, request_ids: set[str], since: float) -> tuple[float, int]:
    total, found = 0.0, 0
    for e in log_events(log_group(fn), '"REPORT RequestId"', since):
        m = REPORT.search(e["message"])
        if m and m[1] in request_ids:
            total += float(m[2]) / 1000 * int(m[3]) / 1024
            found += 1
    return total, found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", required=True, help="a COMPLETE plain 1M/10 job to compare SE with")
    args = ap.parse_args()
    check = Checks()

    t0 = time.time()
    status, headers, body = call(
        "POST",
        "/jobs",
        {**REFERENCE, "paths": 1_000_000, "shards": 10, "variance_reduction": "antithetic"},
    )
    if not check("submit antithetic 1M/10 → 202", status == 202, body.get("job_id") or body):
        return check.summary()
    job_id = body["job_id"]
    submit_rid = next((v for h, v in headers.items() if h.lower() == "x-request-id"), None)
    view, _ = wait(job_id, timeout_s=300)
    res, ref = view.get("result") or {}, view.get("reference") or {}
    check("COMPLETE", view["status"] == "COMPLETE", view["status"])
    check(
        "variance_reduction antithetic, n = 500,000 pairs",
        view.get("variance_reduction") == "antithetic" and res.get("n") == 500_000,
        f"{view.get('variance_reduction')} n={res.get('n')}",
    )
    check(
        "within 3 SE of Black-Scholes",
        ref.get("within_3se") is True,
        f"estimate={res.get('estimate')} se={res.get('std_error')} z={ref.get('z_score')}",
    )
    plain = call("GET", f"/jobs/{args.plain}")[2]["result"]
    ratio = res["std_error"] / plain["std_error"]
    check(
        "SE below the plain job's at equal paths",
        ratio < 1,
        f"antithetic {res['std_error']:.6g} / plain {plain['std_error']:.6g} = {ratio:.3f}",
    )

    status, _, rb = call("POST", f"/jobs/{job_id}/replay")
    replay, _ = wait(rb["job_id"], timeout_s=300) if status == 202 else ({}, 0)
    rc = replay.get("replay_check") or {}
    check(
        "replay bit-identical",
        rc.get("bit_identical") is True,
        f"{rb.get('job_id')} shards={rc.get('shards_compared')} diffs={rc.get('shard_diffs')}",
    )

    # Cost: the estimate (computed from shard duration_ms) against the REPORT lines.
    cost = view.get("cost_estimate") or {}
    time.sleep(20)  # REPORT lines reach CloudWatch Logs within seconds
    worker_rids = {
        json.loads(e["message"]).get("request_id")
        for e in log_events(log_group("worker"), f'{{ $.job_id = "{job_id}" }}', t0 - 5)
    }
    worker_rids.discard(None)
    w_gb_s, w_found = gb_seconds_from_reports("worker", worker_rids, t0 - 5)
    a_gb_s, a_found = gb_seconds_from_reports("api", {submit_rid}, t0 - 5)
    measured = w_gb_s + a_gb_s
    est = cost.get("lambda_gb_seconds")
    check(
        "REPORT lines found (10 worker invocations + the submit)",
        w_found >= 10 and a_found == 1,
        f"worker {w_found} (request ids {len(worker_rids)}), api {a_found}",
    )
    check(
        "cost_estimate GB-s within ±10% of REPORT GB-s",
        est is not None and abs(est - measured) <= 0.10 * measured,
        f"estimate {est} vs REPORT {measured:.6f} (worker {w_gb_s:.6f} + submit {a_gb_s:.6f})",
    )
    print(f"\ncost_estimate: {json.dumps(cost)}")
    print(f"job {job_id}  replay {rb.get('job_id')}  Qrypt spent: 1 KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
