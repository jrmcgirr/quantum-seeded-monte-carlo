"""QMC-127: Step Functions validates the watcher definition exactly as Terraform renders it
(ValidateStateMachineDefinition: read-only, free, needs no deployed machine)."""

from __future__ import annotations

import boto3
import pytest

from tests.integration.test_sfn_definition import render

pytestmark = pytest.mark.deployed


def test_step_functions_accepts_the_rendered_definition():
    sfn = boto3.client("stepfunctions", region_name="us-east-2")
    r = sfn.validate_state_machine_definition(definition=render(), type="STANDARD")
    assert r["result"] == "OK", r["diagnostics"]
    assert not [x for x in r["diagnostics"] if x["severity"] == "ERROR"], r["diagnostics"]
