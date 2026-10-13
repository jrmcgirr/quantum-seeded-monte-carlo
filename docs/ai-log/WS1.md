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
  - `kms:Decrypt`: not needed (job 5358ec35, HANDOFF 1.6 §8.3); see the follow-up entry below.
  - `make e2e` timing from empty belongs to the drift check.
- **Human checks and changes:**
  - James approved installing terraform 1.16.5 (HashiCorp tap), tflint 0.64.0 (GitHub release, checksum verified) and checkov 3.3.28 (pipx).
  - James approved writing `~/.qmc/dev.tfvars` (mode 600) and the first apply.
  - The auto-mode classifier blocked removing `aws:SourceAccount` from the Lambda trust policies. They were kept, and Lambda accepted them.

## 2026-10-13, about 6:20–6:30 AM CT, WS1 Infrastructure (follow-up)

- **Tool and model:** Claude Code, Claude Opus 5.5.
- **Produced:**
  - Rebased and re-applied with the real handlers from main (c768aea): 2 Lambdas changed, then a plan with no changes. MR !3 (stack) and MR !4 (ruff format fix) are merged.
  - QMC-115 test: James confirmed the SNS subscription; `SetAlarmState` on `qmc-dev-dlq-depth` gave ALARM at 11:24:02 UTC and OK at 11:25:08 UTC, and the alarm history shows both SNS actions executed. `qmc-dev-seeding-failures` fired at 11:18:32 UTC on WS2's real fail-closed submits (an EMF metric with no dimensions, as designed).
  - Signed `/health` → 200 once James created `/qmc/qrypt-api-key` (Standard SecureString, `alias/aws/ssm`). His first `put-parameter` with `file:///dev/stdin` failed with an empty value; a temp-file variant (mode 600, `rm -P`) worked.
  - **`kms:Decrypt`: not needed.** WS2's live submit (job `5358ec35af8a450d9eba939da9f610d9`, 202, Qrypt HTTP 200) decrypted the token with `ssm:GetParameter` alone. The conditional KMS statement stays out.
  - G1 run on this stack (reported by WS2): COMPLETE within 3 SE (z 1.735); replay bit-identical (10 shards, `fingerprint_match` true); worker `exp_target` X86_V3 with `dispatch_enforced` true.
- **Human checks:** James approved the push, MR !3, the re-apply, MR !4 and the test alarm; he created the token parameter himself.

## 2026-10-13, about 6:35–6:50 AM CT, WS1 Infrastructure (QMC-118)

- **Tool and model:** Claude Code, Claude Opus 5.5.
- **Produced:**
  - MR !7: the API function ARN is built from its name, so a `build_sha` bump plans exactly 2 changes. Confirmed by WS0's apply from main 2660767: 2 changed, then no changes.
  - QMC-118: dashboard `qmc-<env>` in the observability module, with 8 metric widgets (jobs, paths, entropy and seeding failures, Qrypt latency p50/p95, worker duration, worker errors/throttles/shard failures, queue depth and age, DLQ depth) and one alarm-status widget. No Logs Insights widgets, which bill per view.
  - Checks: `validate` and tflint clean; checkov 137 passed, 0 failed, 20 skipped. A `-lock=false` plan (the integrator was applying) showed the dashboard as 1 to add, plus the 2 sha-bump Lambda updates.
  - Pointed out to WS0 and WS4 that GitLab's golden job runs emulated on James's Apple Silicon runner. WS0 had already recorded it (HANDOFF 1.8, D19); the GitHub Actions x86 runners are the native check.
- **Human checks:** James approved pushing MR !7 and starting QMC-118. Since G1 the integrator applies from main, so this session no longer applies.

## 2026-10-13, about 7:00–7:40 AM CT, WS1 Infrastructure (handoffs, contract change 004)

- **Tool and model:** Claude Code, Claude Opus 5.5.
- **Produced:**
  - QMC-118 merged as MR !8 and applied by WS0 from main 855e8d6 at 12:04:35Z: 1 added, 2 changed, then a plan with no changes, so `dashboard_body` doesn't drift. #38 closed.
  - QMC-117 handoff: sent WS5 (through WS0) the Checkov suppression list with each reason, read from the code. That's 20 skips across **13** distinct checks; I first said 14, counting CKV_AWS_116 twice. WS5's re-run on 855e8d6: 137 passed, 0 failed, 20 skipped. WS0 closed #35.
  - Golden-hash emulation risk flagged to WS0 and WS4. WS4's `tools/shard_crosscheck.py` reproduced real-Lambda job edaff921 (10 shards × 100k) bit for bit under Rosetta x86 and natively on arm64; the native-x86 GitHub run is the last check.
  - Contract change 004 (HANDOFF 1.10): `qrypt_max_per_window` default 15 → 10 (Qrypt trial key, 1 call/s), validation 1–15 kept. `~/.qmc/dev.tfvars` doesn't override it. A `-lock=false` plan shows only `QRYPT_MAX_PER_WINDOW` "15" → "10" on qmc-dev-api, plus the `build_sha` bump on both Lambdas.
- **Human checks:** James approved pushing MR !8 and this MR. The integrator applies from main; this session ran only read-only plans.

## 2026-10-13, about 8:10–8:20 AM CT, WS1 Infrastructure (contract change 005)

- **Tool and model:** Claude Code, Claude Opus 5.5.
- **Produced:**
  - Contract change 004 applied by WS0 from main f3873f8 at 12:55:14Z: `QRYPT_MAX_PER_WINDOW` "15" → "10" plus the sha bump, 2 changed, then a plan with no changes.
  - Contract change 005 (HANDOFF 1.11, accepted by James): new root variable `qrypt_window_s` (default 1, integer 1–10) passed to the api module as `QRYPT_WINDOW_S`; `qrypt_max_per_window` default 10 → 1 (1–15 validation kept). `validate` and tflint clean. A `-lock=false` plan shows only qmc-dev-api's env changing (`QRYPT_MAX_PER_WINDOW` "10" → "1", plus `QRYPT_WINDOW_S` = "1"), with the sha bump once committed.
  - Deployment coupling: the API on main ignores `QRYPT_WINDOW_S` (10 s windows), so this must be applied together with WS2's limiter change; alone, cap 1 would mean 1 call per 10 s. Recorded in the commit message and the MR.
- **Human checks:** James approved the push and MR; WS0 merges this with WS2's MR and applies from main.
