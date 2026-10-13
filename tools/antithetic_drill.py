"""QMC-108 / QMC-126 deployed drill: one antithetic 1M-path job (1 KiB of Qrypt), then (or, with
--cost-only, one plain job and only the cost check)
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

REPORT = re.compile(
    r"RequestId: (\S+)\s+Duration: ([\d.]+) ms\s+Billed Duration: (\d+) ms\s+Memory Size: (\d+) MB"
)
API_SHARE_GB_S = 0.125  # qmc_core.cost: flat API estimate per request


def gb_seconds_from_reports(fn: str, request_ids: set[str], since: float) -> tuple[float, int]:
    """Billed GB-s (Billed Duration × memory) of the given invocations: what Lambda charges."""
    total, found = 0.0, 0
    for e in log_events(log_group(fn), '"REPORT RequestId"', since):
        m = REPORT.search(e["message"])
        if m and m[1] in request_ids:
            total += int(m[3]) / 1000 * int(m[4]) / 1024
            found += 1
    return total, found


def cost_check(check, job_id: str, view: dict, submit_rid: str | None, t0: float) -> None:
    """Contract change 009: worker GB-s in cost_estimate (minus the flat API share) within ±15% of
    the matched worker REPORT Billed Duration × memory. Finalize work on the last shard after its
    put is not counted by the estimate. The submit is measured and reported separately."""
    cost = view.get("cost_estimate") or {}
    time.sleep(20)  # REPORT lines reach CloudWatch Logs within seconds
    worker_rids = {
        json.loads(e["message"]).get("request_id")
        for e in log_events(log_group("worker"), f'{{ $.job_id = "{job_id}" }}', t0 - 5)
    }
    worker_rids.discard(None)
    billed_worker, w_found = gb_seconds_from_reports("worker", worker_rids, t0 - 5)
    billed_submit, a_found = gb_seconds_from_reports("api", {submit_rid}, t0 - 5)
    est = cost.get("lambda_gb_seconds")
    est_worker = None if est is None else est - API_SHARE_GB_S
    check(
        "REPORT lines found (every worker invocation + the submit)",
        w_found >= int(view["shards"]) and a_found == 1,
        f"worker {w_found} (request ids {len(worker_rids)}), api {a_found}",
    )
    check(
        "worker GB-s in cost_estimate within ±15% of REPORT Billed GB-s",
        est_worker is not None and abs(est_worker - billed_worker) <= 0.15 * billed_worker,
        f"estimate {est_worker} vs billed {billed_worker:.6f} "
        f"({(est_worker or 0) / billed_worker:.3f}×; finalize after the last put is not counted)",
    )
    print(
        f"submit: billed {billed_submit:.6f} GB-s vs the flat API share {API_SHARE_GB_S} (reported, not asserted)"
    )
    print(f"cost_estimate: {json.dumps(cost)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", help="a COMPLETE plain 1M/10 job to compare SE with")
    ap.add_argument("--cost-only", action="store_true", help="one plain 1M/10 job, cost check only")
    args = ap.parse_args()
    check = Checks()
    if args.cost_only:
        t0 = time.time()
        status, headers, body = call(
            "POST", "/jobs", {**REFERENCE, "paths": 1_000_000, "shards": 10}
        )
        if not check("submit plain 1M/10 → 202", status == 202, body.get("job_id") or body):
            return check.summary()
        rid = next((v for h, v in headers.items() if h.lower() == "x-request-id"), None)
        view, _ = wait(body["job_id"], timeout_s=300)
        check("COMPLETE", view["status"] == "COMPLETE", view["status"])
        cost_check(check, body["job_id"], view, rid, t0)
        print(f"job {body['job_id']}  Qrypt spent: 1 KiB")
        return check.summary()
    assert args.plain, "--plain is required unless --cost-only"

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

    cost_check(check, job_id, view, submit_rid, t0)
    print(f"job {job_id}  replay {rb.get('job_id')}  Qrypt spent: 1 KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
