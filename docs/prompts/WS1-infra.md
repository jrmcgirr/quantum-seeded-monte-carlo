# WS1 — Infrastructure (Terraform)

You are building everything that runs in AWS, as Terraform: the DynamoDB table, the SQS queue and DLQ, both Lambda functions with one least-privilege role each, the Function URL with `AWS_IAM` auth, the SNS alerts topic, seven alarms and the $1 Budget, plus the deterministic build of the Lambda zips. The graders score this hard: a fully modular root, clean destroy, no drift, no wildcards, the secret never in state, and $0 at rest. You gate G1 (**first apply, Tuesday 2026-10-13, 6:00 PM Central**) and G2 (**alarms, Budget, drift check, Wednesday 12:00 PM**).

**Start:** Tue 9:00 AM, without waiting for G0: everything you need (names, settings, IAM, environment variables) is in HANDOFF §8. Work on branch `ws1-infra` in `../quantum-seeded-monte-carlo-ws1`. Rebase on `g0a-interfaces` (9:45 AM) to get the skeleton, the final Makefile and WS0's stub handlers; build zips from the stubs until G0 lands, then rebase again. **Apply discipline (HANDOFF D33):** until G1 you apply from your branch rebased on `main`, after telling James; from G1 on, only the integrator applies, from `main`. Settings come from `$HOME/.qmc/dev.tfvars` (James's), never from a file in your checkout.

## Read first

1. `docs/HANDOFF.md`: §8 (infrastructure) is your specification; also §1 (I6, I7), §2 (Day 0, who applies), §6.1 (table), §9 (metrics, alarms, logs), §10 (cost rules), §14 (risks R6–R13).
2. `docs/PRIMER.md`, sections "The free tier is won or lost on SQS polling and CloudWatch counts" and "Terraform hygiene and IAM decide 'clean destroy, no drift'".
3. `CLAUDE.md`; your tickets in `docs/ROADMAP.md`: QMC-110 to QMC-118.

## You own

`infra/**` (including `infra/.tflint.hcl`), `scripts/build_lambdas.py` and `docs/ai-log/WS1.md`. You change no Python under `src/` and you do not edit the Makefile (WS0 wrote its final recipes; they call your fixed script paths). Anything another workstream reads (an output, a variable, an environment variable name) is contract: propose changes in `docs/contract-changes/`.

## Build in this order

1. **Root skeleton and `data` module (QMC-111).** `versions.tf` (Terraform ≥ 1.10, AWS provider `~> 6.0`), `providers.tf` with `default_tags`, empty `backend "local" {}` (the Makefile passes `-backend-config="path=$HOME/.qmc/state/$(ENV).tfstate"` and `-var-file=$HOME/.qmc/$(ENV).tfvars`), documented variables (HANDOFF §8.6), the table, both queues with the redrive policy resource, and the TLS-only queue policies. Apply the data module alone first to shake out credentials and the region.
2. **Build script (QMC-110).** `scripts/build_lambdas.py` per HANDOFF §8.5: deterministic zips (sorted entries, fixed 1980 timestamps, fixed permissions, no bytecode). The worker zip installs NumPy from `requirements/worker.txt` for `manylinux_2_28_x86_64` / cp313 with `--require-hashes`. Add a `--check` mode that builds twice and compares hashes. Use `pip install --no-compile`. Write `build/manifest.json` (git sha, branch, dirty, zip hashes) outside the zips; the Makefile passes the sha as `-var build_sha=…` and you set it as `QMC_BUILD_SHA`.
3. **`workers` and `api` modules (QMC-112, QMC-113).** Log groups first (14 days), then roles (HANDOFF §8.3, `aws_iam_policy_document`, exact ARNs), then functions (`python3.13`, `x86_64`, `source_code_hash = filebase64sha256(...)`, worker `runtime_management_config { update_runtime_on = "FunctionUpdate" }` and `QMC_ENFORCE_PINNED_DISPATCH=true`), the event source mapping (batch 1, `ReportBatchItemFailures`, `maximum_concurrency`, `depends_on` the role policy), the Function URL (`AWS_IAM`, `BUFFERED`), the client role and the optional grader user (`force_destroy = true`, no access key resource).
4. **Root wiring and first apply (QMC-114).** Outputs and `terraform.tfvars.example` (explain `worker_reserved_concurrency`: leave null unless `aws lambda get-account-settings` shows a quota ≥ 1,000). Time the first apply from empty. Hand WS4 the URL via `make outputs`; check `GET /health` shows the deployed `build_sha`.
5. **`observability` and `budget` (QMC-115, QMC-116).** SNS topic (unencrypted) with a policy covering the account, `cloudwatch.amazonaws.com` and `budgets.amazonaws.com`; the email subscription (James confirms it immediately, and again after every apply from empty); the seven alarms from HANDOFF §9.2 with alarm and OK actions; the Budget with three notifications, `depends_on` the topic policy.
6. **Checkov and least privilege (QMC-117).** Run Checkov at G1 to get the real list; fix what $0 allows (for example attach the grader policy through an IAM group); suppress the rest inline with reasons; hand WS5 the list.
7. **Dashboard (QMC-118, stretch).**

## Requirements that are easy to get wrong

- **Never `data "aws_ssm_parameter"`.** It writes the decrypted token into state. Build the parameter ARN as a string from the region, the account id and `var.qrypt_api_key_param_name`. CI fails the build if the data source appears.
- **Log groups before functions,** with retention, and no `logs:CreateLogGroup` in any role. Otherwise Lambda creates them, they never expire and they survive destroy.
- **One event source mapping.** Never map the DLQ: it would drain it (the alarm must fire) and double the idle SQS polling, breaking $0.
- **No `server_side_encryption` block on the table** (the default AWS-owned key is free and still encrypted); verify with `describe-table` after apply. **No customer-managed KMS keys** anywhere.
- **Both invoke actions** for Function URL callers (October 2025 change): `lambda:InvokeFunctionUrl` (condition `lambda:FunctionUrlAuthType = AWS_IAM`) and `lambda:InvokeFunction` (condition `lambda:InvokedViaFunctionUrl = true`).
- `visibility_timeout_s ≥ 6 × worker_timeout_s` (variable validation). `maxReceiveCount` is an integer inside `jsonencode`, and the same variable feeds the worker's `MAX_RECEIVE_COUNT`.
- `worker_reserved_concurrency` **defaults to `null`** (a grader's new account with a 10 or 50 quota rejects any reservation); James's `dev.tfvars` sets 10. Never set the API's reserved concurrency (0 disables the URL).
- The API role needs `dynamodb:BatchGetItem` (for `GET /jobs`); WS0's IAM allow-list test is the contract, so keep §8.3 and your policies identical.
- The Budget: no `time_period_start`, never `timestamp()`; `count` gate so only one environment creates it.
- **Drift:** after every apply, a second `plan` must show no changes. Watch `source_code_hash`, the redrive policy JSON, SQS SSE, and Budget fields.
- Only you (until G1, rebased, announced) and the integrator apply, one at a time. Tell James before a destroy; every destroy/apply creates a new unconfirmed SNS subscription that James must confirm.

## Done when

- `make build && make apply` from empty succeeds in us-east-2 in under 10 minutes; the second `make plan` shows no changes.
- `clients/qmc.py health` with the client role returns 200; an unsigned request returns 403.
- Alarms exist and are not in ALARM at rest; the Budget exists; a test alarm state change delivers an email.
- `checkov -d infra` passes with only documented suppressions; no `*` in any IAM action or resource except the log-stream suffix.
- You report: apply time, resource count, any HANDOFF value you had to change, the `kms:Decrypt` outcome (needed or not), and an AI usage log entry in `docs/ai-log/WS1.md`.

## Review checklist for James

- [ ] `grep -rn 'aws_ssm_parameter' infra/` finds no data source
- [ ] `terraform show -json | grep -i qrypt` shows only the parameter name, never a value
- [ ] Every IAM policy document lists exact ARNs; `Action` never contains `*`
- [ ] Log groups are resources with `retention_in_days = 14`
- [ ] Exactly one `aws_lambda_event_source_mapping`, on the main queue
- [ ] DynamoDB is PROVISIONED 10/10, no GSI, PITR off; `describe-table` shows no KMS key ARN
- [ ] Building twice gives identical zip hashes
- [ ] Two consecutive plans after apply show no changes
