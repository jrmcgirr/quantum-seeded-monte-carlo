"""make force-failure (QMC-144): forced shard failure → PARTIAL → DLQ → alarm → email.
Spends 1 KiB of Qrypt per drill (2 with --duplicate).

Needs ``enable_fault_injection = true`` in the deployment and ``make warm-dlq`` ≥ 15 min before
(R23). Purges the DLQ at the end so the alarm returns to OK (``--keep`` to skip).
``--duplicate``: also re-send a COMPLETE job's shard message and show nothing changes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from _common import ENV, Checks, aws, call, log_events, log_group, outputs, poll, wait

ALARM = f"qmc-{ENV}-dlq-depth"
FAIL_SHARD = 3
JOB = {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0, "paths": 100_000, "shards": 10}


def dlq_depth() -> int:
    a = aws("sqs").get_queue_attributes(
        QueueUrl=outputs()["dlq_url"],
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])


def alarm_state() -> str:
    return aws("cloudwatch").describe_alarms(AlarmNames=[ALARM])["MetricAlarms"][0]["StateValue"]


def forced_failure(check: Checks) -> None:
    status, _, body = call(
        "POST", "/jobs", {**JOB, "fault_injection": {"fail_shards": [FAIL_SHARD]}}
    )
    if not check("submit with fail_shards=[3] → 202", status == 202, body.get("job_id") or body):
        return
    t0 = time.monotonic()
    view, _ = wait(body["job_id"], timeout_s=300)
    check(
        "job PARTIAL with [3]",
        view["status"] == "PARTIAL" and view["failed_shards"] == [FAIL_SHARD],
        f"{view['status']} failed={view['failed_shards']} at +{time.monotonic() - t0:.0f} s",
    )
    check(
        "no result, partial over 9 shards",
        view["result"] is None and (view.get("partial_result") or {}).get("n") == 90_000,
        view.get("partial_result"),
    )
    depth, _ = poll("DLQ depth", dlq_depth, 120, 5)
    check("DLQ depth 1", depth == 1, f"{depth} at +{time.monotonic() - t0:.0f} s")
    try:
        poll(ALARM, lambda: alarm_state() == "ALARM", 20 * 60, 15)
        check(f"{ALARM} ALARM", True, f"at +{time.monotonic() - t0:.0f} s after submit")
        print(">>> Check the alert email now and take the screenshot for WS5.")
    except TimeoutError as e:
        check(f"{ALARM} ALARM", False, e)


def duplicate(check: Checks) -> None:
    from boto3.dynamodb.types import TypeDeserializer

    from qmc_core import messages

    status, _, body = call("POST", "/jobs", {**JOB, "shards": 4})
    if not check("duplicate drill: submit → 202", status == 202, body):
        return
    job_id = body["job_id"]
    before, _ = wait(job_id)
    check("duplicate drill: COMPLETE", before["status"] == "COMPLETE", before["status"])
    raw = aws("dynamodb").get_item(
        TableName=outputs()["table_name"],
        ConsistentRead=True,
        Key={"pk": {"S": f"JOB#{job_id}"}, "sk": {"S": "META"}},
    )["Item"]
    item = {k: TypeDeserializer().deserialize(v) for k, v in raw.items()}
    sent_at = time.time()
    aws("sqs").send_message(
        QueueUrl=outputs()["queue_url"], MessageBody=messages.encode(messages.for_job(item)[0])
    )
    lines, secs = poll(
        "duplicate_delivery log line",
        lambda: log_events(
            log_group("worker"),
            f'{{ $.event = "duplicate_delivery" && $.job_id = "{job_id}" }}',
            sent_at - 5,
        ),
        180,
        10,
    )
    check("worker logged duplicate_delivery", bool(lines), f"after {secs:.0f} s")
    _, _, after = call("GET", f"/jobs/{job_id}")
    check(
        "result and n unchanged",
        after["result"] == before["result"] and after["progress"] == before["progress"],
        after["result"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--duplicate", action="store_true", help="also run the duplicate-delivery drill"
    )
    ap.add_argument("--keep", action="store_true", help="do not purge the DLQ afterwards")
    ap.add_argument("--no-warm-check", action="store_true")
    args = ap.parse_args()
    check = Checks()

    warmed = Path.home() / ".qmc" / f"dlq-warmed-at-{ENV}"
    age = time.time() - float(warmed.read_text()) if warmed.exists() else None
    if not args.no_warm_check and (age is None or age < 15 * 60):
        print(
            f"DLQ warmed {'never' if age is None else f'{age / 60:.0f} min ago'}; "
            "run make warm-dlq and wait 15 min (R23), or pass --no-warm-check"
        )
        return 2
    check(
        "precondition: DLQ empty, alarm not ALARM",
        dlq_depth() == 0 and alarm_state() != "ALARM",
        f"depth={dlq_depth()} alarm={alarm_state()}",
    )

    forced_failure(check)
    if args.duplicate:
        duplicate(check)
    if not args.keep:
        aws("sqs").purge_queue(QueueUrl=outputs()["dlq_url"])
        print("DLQ purged; the alarm returns to OK within a few minutes (OK email follows).")
    print("Qrypt spent:", 2 if args.duplicate else 1, "KiB")
    return check.summary()


if __name__ == "__main__":
    sys.exit(main())
