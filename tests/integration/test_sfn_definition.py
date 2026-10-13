"""QMC-127 (contract change 007): the watcher definition, rendered as Terraform's templatefile
does, is valid JSON with every transition resolved and polls the job item the API writes. The
deployed twin (tests/deployed/test_sfn_definition.py) asks Step Functions to validate it."""

from __future__ import annotations

import json
from pathlib import Path

ASL = Path(__file__).parents[2] / "infra/modules/api/watcher.asl.json"


def render(table_name: str = "qmc-dev") -> str:
    text = ASL.read_text()
    out = text.replace("${table_name}", table_name)
    assert "${" not in out and "%{" not in out, "unrendered templatefile placeholder"
    return out


def test_rendered_definition_is_closed():
    d = json.loads(render())
    states = d["States"]
    targets = {d["StartAt"]} | {s["Next"] for s in states.values() if "Next" in s}
    for s in states.values():
        targets |= {c["Next"] for c in s.get("Choices", [])} | (
            {s["Default"]} if "Default" in s else set()
        )
    assert targets <= set(states), targets - set(states)
    assert {"Succeed", "Fail"} <= {s["Type"] for s in states.values()}
    assert d["TimeoutSeconds"] <= 1200


def test_polls_the_job_meta_item_and_waits_on_non_terminal_only():
    d = json.loads(render("t"))
    get = d["States"]["GetJob"]["Parameters"]
    assert get["TableName"] == "t" and get["ConsistentRead"] is True
    assert get["Key"]["sk"] == {"S": "META"}
    assert get["Key"]["pk"] == {"S.$": "States.Format('JOB#{}', $.job_id)"}  # submit's input
    choices = d["States"]["Check"]["Choices"]
    waiting = {c["StringEquals"] for c in choices[0]["Or"]}
    assert waiting == {"QUEUED", "PENDING_ENTROPY"}  # COMPLETE / PARTIAL / FAILED end it
    assert d["States"]["Wait"]["Seconds"] >= 2  # HANDOFF: poll at 2 s at most
