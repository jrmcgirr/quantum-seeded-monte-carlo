# WS1 AI usage log

## 2026-10-13, about 5:35–6:20 AM CT, WS1 Infrastructure

- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`), assigned by the WS0 session.
- **Asked:** execute `docs/prompts/WS1-infra.md`: QMC-110 to QMC-117 (build script, Terraform root and five modules, first apply, Checkov).
- **Produced:**
  - `scripts/build_lambdas.py`: deterministic zips; pip pinned through `uvx --from pip==25.2` (the uv venv has no pip); `--no-compile --no-deps`; pip's `bin/` dropped (shebangs differ per machine). `--check` on the G0a stubs: identical hashes on both builds; api.zip 13.7 KB with no NumPy, worker.zip 16.7 MB.
  - `infra/`: root (versions, providers, variables, outputs, tfvars example), modules `data`, `workers`, `api`, `observability`, `budget`, `.tflint.hcl` (AWS ruleset 0.49.0), lock file for darwin_arm64 and linux_amd64 (aws 6.68.0).
  - Checks: `terraform fmt -check`, `validate` and `tflint --recursive` clean; `checkov -d infra`: 131 passed, 0 failed, 20 inline suppressions with reasons.
  - First apply from `ws1-infra` rebased on `main` (approved by James in session): 32 managed resources in us-east-2. The first run (73 s) stopped on the SCP denial below; the rerun after the fix took 15 s. The second `make plan` reported "No changes".
- **Deviations from HANDOFF, found by running the code:**
  1. `runtime_management_config` is not a block on `aws_lambda_function` in provider 6.68, so the worker uses the `aws_lambda_runtime_management_config` resource instead (verified: `GetRuntimeManagementConfig` → `FunctionUpdate`).
  2. The new-AWS-experience Paid-plan SCP (`DenyIAMRestrictedActions`) denies `iam:CreateGroup`, `PutGroupPolicy` and `AddUserToGroup`. The grader's invoke policy therefore goes inline on the user (`CKV_AWS_40` suppressed with that reason). `CreateUser`, `PutUserPolicy` and `CreateAccessKey` are not denied, so D26 still works.
- **Verified after apply (AWS API reads):**
  - Table: PROVISIONED 10/10, TTL on `ttl`, PITR disabled, no `SSEDescription` (AWS-owned key).
  - Queues: SSE-SQS on both; redrive `maxReceiveCount` 3 as an integer.
  - Exactly one event source mapping (main queue → worker; batch size 1, `ReportBatchItemFailures`, maximum concurrency 5).
  - Worker reserved concurrency 10; both log groups have 14-day retention; 7 alarms in OK; Budget `qmc-monthly` at 1.0 USD.
  - Unsigned `GET /health` → 403; signed as `qmc-dev-client` → 200 (WS0 stub body).
  - `terraform show -json` contains only the parameter name and ARN, never a value.
- **Open:**
  - The SNS subscription is PendingConfirmation (James).
  - QMC-115's test alarm email: run after confirmation.
  - `kms:Decrypt`: not yet tested; it needs WS2's submit path (`GetParameter` with `WithDecryption`).
  - `make e2e` timing from empty belongs to the drift check.
- **Human checks and changes:**
  - James approved installing terraform 1.16.5 (HashiCorp tap), tflint 0.64.0 (GitHub release, checksum verified) and checkov 3.3.28 (pipx).
  - James approved writing `~/.qmc/dev.tfvars` (mode 600) and the first apply.
  - The auto-mode classifier blocked removing `aws:SourceAccount` from the Lambda trust policies. They were kept, and Lambda accepted them.
