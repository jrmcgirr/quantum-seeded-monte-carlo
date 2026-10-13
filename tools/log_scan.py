"""Secret-leak scan of every /aws/lambda/qmc-<env>-* log group (I6 evidence for SECURITY.md).

Reads every event since --since (default: today 00:00 UTC) and counts regex matches in Python,
because CloudWatch filter terms are case-sensitive. Prints counts and where matches are, never
the matching text. Does not read the real token: proving it is absent would mean handling it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys

from _common import ENV, aws

PATTERNS = {
    "Bearer": re.compile(r"Bearer"),
    "authorization (any case)": re.compile(r"authorization", re.I),
    "api_key": re.compile(r"api_key"),
    '"token"': re.compile(r'"token"', re.I),
    "NOT-A-REAL-TOKEN (decoy marker)": re.compile(r"NOT-A-REAL-TOKEN"),
}
CONTROL = re.compile(
    r'"event":"qrypt_call"'
)  # must match in the API group: proves the scan reads text


def main() -> int:
    ap = argparse.ArgumentParser()
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    ap.add_argument(
        "--since", default=today.isoformat(), help="ISO time (default: today 00:00 UTC)"
    )
    args = ap.parse_args()
    since = dt.datetime.fromisoformat(args.since)

    logs = aws("logs")
    groups = [
        g["logGroupName"]
        for p in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=f"/aws/lambda/qmc-{ENV}-"
        )
        for g in p["logGroups"]
    ]
    total = 0
    print(f"since {since.isoformat()}; groups: {groups}")
    print("| log group | events scanned | " + " | ".join(PATTERNS) + " | control: qrypt_call |")
    print("| --- | --- | " + " | ".join("---" for _ in PATTERNS) + " | --- |")
    for g in groups:
        counts, n, where, control = dict.fromkeys(PATTERNS, 0), 0, [], 0
        pages = logs.get_paginator("filter_log_events").paginate(
            logGroupName=g, startTime=int(since.timestamp() * 1000)
        )
        for page in pages:
            for e in page["events"]:
                n += 1
                control += bool(CONTROL.search(e["message"]))
                for name, rx in PATTERNS.items():
                    if rx.search(e["message"]):
                        counts[name] += 1
                        where.append((name, e["logStreamName"], e["timestamp"]))
        total += sum(counts.values())
        print(f"| {g} | {n} | " + " | ".join(str(c) for c in counts.values()) + f" | {control} |")
        if g.endswith("-api") and not control:
            print("CONTROL FAILED: no qrypt_call line found; the scan is not reading log text")
            total += 1
        for name, stream, ts in where[:20]:
            print(f"  match {name!r} in {stream} at {ts} (text not printed)")
    print(f"\ntotal matches: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
