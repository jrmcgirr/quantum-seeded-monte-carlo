"""make e2e (QMC-142): the G1 proof on the deployed stack. Spends 1 KiB of Qrypt per run.

health → list → 404 → 1M-path reference job (progress observed) → COMPLETE within 3 SE →
replay → bit-identical. ``--fresh``: right after an apply from empty, the list must be empty.
"""

from __future__ import annotations

import argparse
import sys
import time

from _common import REFERENCE, Checks, call, wait


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="GET /jobs must be empty")
    args = ap.parse_args()
    check = Checks()

    status, _, body = call("GET", "/health")
    check(
        "health 200",
        status == 200,
        f"build_sha={body.get('build_sha')} checks={body.get('checks')}",
    )

    status, _, body = call("GET", "/jobs")
    jobs = body.get("jobs")
    check(
        "GET /jobs is an array", status == 200 and isinstance(jobs, list), f"{len(jobs or [])} jobs"
    )
    if args.fresh:
        check("fresh: GET /jobs empty", jobs == [], jobs)

    unknown = "0" * 32
    status, _, body = call("GET", f"/jobs/{unknown}")
    err = body.get("error", {})
    check(
        "unknown id → 404",
        status == 404 and err.get("code") == "JOB_NOT_FOUND" and unknown in err.get("message", ""),
        err,
    )

    t0 = time.monotonic()
    status, _, body = call("POST", "/jobs", {**REFERENCE, "paths": 1_000_000, "shards": 10})
    if not check("submit 1M/10 → 202", status == 202, body.get("job_id") or body):
        return check.summary()
    job_id = body["job_id"]

    trail: list[str] = []

    def observe(view, _t):
        p = view.get("progress", {})
        step = f"{view['status']}:{p.get('done')}/{p.get('total')}"
        if not trail or trail[-1] != step:
            trail.append(step)

    view, _ = wait(job_id, on_view=observe)
    wall = time.monotonic() - t0
    non_terminal = [s for s in trail if not s.startswith(("COMPLETE", "PARTIAL", "FAILED"))]
    check("progress observed before terminal", bool(non_terminal), " → ".join(trail))
    check("COMPLETE", view["status"] == "COMPLETE", f"{wall:.1f} s submit → COMPLETE")
    ref, res = view.get("reference") or {}, view.get("result") or {}
    check(
        "within 3 SE of Black-Scholes",
        ref.get("within_3se") is True,
        f"estimate={res.get('estimate')} se={res.get('std_error')} bs={ref.get('black_scholes')} "
        f"z={ref.get('z_score')}",
    )
    check("1M paths counted once", res.get("n") == 1_000_000, res.get("n"))

    status, _, body = call("POST", f"/jobs/{job_id}/replay")
    if not check("replay → 202", status == 202, body.get("job_id") or body):
        return check.summary()
    replay, _ = wait(body["job_id"])
    rc = replay.get("replay_check") or {}
    check("replay COMPLETE", replay["status"] == "COMPLETE", replay["status"])
    check(
        "replay bit-identical",
        rc.get("bit_identical") is True,
        f"shards_compared={rc.get('shards_compared')} diffs={rc.get('shard_diffs')} "
        f"fingerprint_match={rc.get('fingerprint_match')}",
    )

    print(f"\njob {job_id}  replay {body['job_id']}  Qrypt spent: 1 KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
