"""Render the qmc-<env> CloudWatch dashboard's metric widgets to PNGs (read-only).

BILLED: GetMetricWidgetImage is outside the CloudWatch free API requests (≈ $0.0006 for one full run,
docs/COST.md). Do not re-run it; ask for a console screenshot instead.

    AWS_PROFILE=qmc uv run python docs/images/render_dashboard.py [--env dev] [--hours 6]

Writes docs/images/dashboard-NN-<slug>.png per metric widget and docs/images/dashboard-alarms.md
(GetMetricWidgetImage cannot draw the alarm-status widget, so alarm states come from DescribeAlarms).
"""

import argparse
import datetime
import json
import pathlib
import re

import boto3

OUT = pathlib.Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--region", default="us-east-2")
    a = ap.parse_args()

    cw = boto3.client("cloudwatch", region_name=a.region)
    name = f"qmc-{a.env}"
    body = json.loads(cw.get_dashboard(DashboardName=name)["DashboardBody"])
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    n = 0
    for w in body["widgets"]:
        if w.get("type") != "metric":
            continue
        n += 1
        props = dict(w["properties"], start=f"-PT{a.hours}H", end="P0D", width=800, height=400)
        props.setdefault("region", a.region)
        props["title"] = f"{props.get('title', '')} (last {a.hours} h to {now})"
        png = cw.get_metric_widget_image(MetricWidget=json.dumps(props), OutputFormat="png")[
            "MetricWidgetImage"
        ]
        slug = re.sub(r"[^a-z0-9]+", "-", w["properties"].get("title", "widget").lower()).strip("-")
        path = OUT / f"dashboard-{n:02d}-{slug}.png"
        path.write_bytes(png)
        print(path.name)

    alarms = cw.describe_alarms(AlarmNamePrefix=f"{name}-")["MetricAlarms"]
    lines = [
        f"# {name} alarm states at {now}",
        "",
        "| Alarm | State | Since (UTC) |",
        "| --- | --- | --- |",
    ]
    for al in sorted(alarms, key=lambda x: x["AlarmName"]):
        since = al["StateUpdatedTimestamp"].astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        lines.append(f"| `{al['AlarmName']}` | {al['StateValue']} | {since} |")
    (OUT / "dashboard-alarms.md").write_text("\n".join(lines) + "\n")
    print(f"dashboard-alarms.md ({len(alarms)} alarms)")


if __name__ == "__main__":
    main()
