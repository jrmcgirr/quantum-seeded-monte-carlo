"""QMC-127 deployed drill (contract change 007), with ``enable_step_functions = true`` applied:
one normal 100k/10 job → its watcher execution (named by job_id) SUCCEEDED; one job with
``fault_injection.fail_shards = [3]`` (needs FAULT_INJECTION_ENABLED) → PARTIAL → execution
FAILED with ``JobNotComplete``. The forced shard lands in the DLQ; the drill purges it (the DLQ
alarm may still fire and email). Qrypt: 2 KiB.
"""

from __future__ import annotations

import sys
import time

from _common import Checks, aws, call, outputs, poll, wait

JOB = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0, "paths": 100_000, "shards": 10}


def execution(job_id: str) -> dict:
    arn = outputs()["state_machine_arn"].replace(":stateMachine:", ":execution:") + f":{job_id}"
    return aws("stepfunctions").describe_execution(executionArn=arn)


def run(check: Checks, name: str, body: dict, job_status: str, sfn_status: str) -> None:
    status, _, r = call("POST", "/jobs", body)
    if not check(f"{name}: submit → 202", status == 202, r.get("job_id") or r):
        return
    view, _ = wait(r["job_id"], timeout_s=300)
    check(f"{name}: job {job_status}", view["status"] == job_status, view["status"])
    # The machine polls every 10 s, so it ends within ~10 s of the job.
    ex, _ = poll(
        f"{name} execution",
        lambda: (e := execution(r["job_id"]))["status"] != "RUNNING" and e,
        90,
        5,
    )
    check(
        f"{name}: execution {sfn_status}",
        ex["status"] == sfn_status,
        f"{ex['executionArn'].rsplit(':', 1)[1]} {ex['status']} {ex.get('error', '')} "
        f"{(ex['stopDate'] - ex['startDate']).total_seconds():.0f} s",
    )


def main() -> int:
    check = Checks()
    assert outputs().get("state_machine_arn"), "enable_step_functions is off (no state_machine_arn)"
    run(check, "normal", JOB, "COMPLETE", "SUCCEEDED")
    run(check, "forced", {**JOB, "fault_injection": {"fail_shards": [3]}}, "PARTIAL", "FAILED")
    time.sleep(5)
    aws("sqs").purge_queue(QueueUrl=outputs()["dlq_url"])
    print("DLQ purged. Qrypt spent: 2 KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
