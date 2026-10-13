# Security

Checked against `infra/` on `main` `581563d`, which is unchanged through the G3 tag `g3-proven` (`2fbd97e`), the deployed functions' configuration, and the live drills in [TEST_RESULTS.md](TEST_RESULTS.md).

## The Qrypt token's path

1. **Created out-of-band.** James stores the token as an SSM **SecureString** `/qmc/qrypt-api-key` with the AWS CLI (README step 2; `read -s` keeps it out of shell history and `ps`). It is never a Terraform variable, a tfvars value, an environment variable or a file in the repo.
2. **Terraform knows only the name.** The parameter ARN is built from the Region, the account id and `var.qrypt_api_key_param_name`. That is all the API role's `ssm:GetParameter` grant needs.
3. **Fetched at runtime.** The API Lambda calls `GetParameter(WithDecryption=True)` and caches the value in memory for `SECRET_CACHE_TTL_S` (300 s on the deployed function). It sends the token only to Qrypt, over TLS, in an `Authorization: Bearer` header.

**Why out-of-band:** anything Terraform reads or writes lands in its state file in plain text. The trap is `data "aws_ssm_parameter"`, which looks harmless but stores the **decrypted** value in state. It is banned (HANDOFF §8.1, R10), and CI's `ssm-data-source-guard` job fails the pipeline if one appears. A search of `infra/` on `855e8d6` finds none.

**If the token fails, nothing is written (I1).** This holds even for a submit that opts into `on_entropy_failure: "queue"` (contract change 008): `QRYPT_AUTH_FAILED`, `QRYPT_FORBIDDEN`, `QRYPT_BAD_RESPONSE`, `QRYPT_BAD_REQUEST` and `SECRET_UNAVAILABLE` always fail fast. Only rate limits, Qrypt 5xx, timeouts and network errors can park a job as `PENDING_ENTROPY`, and that record holds no seed. Two live checks, both in TEST_RESULTS:

- **Secret failure** (parameter missing): 503 `SECRET_UNAVAILABLE`, no job written.
- **Decoy-token drill** (a wrong token stored under `/qmc/qrypt-api-key-invalid`, build `d0af0b1`): 503, no job written, and the `qmc-dev-seeding-failures` alarm went to ALARM. This drill ran before contract change 003, so it reported `QRYPT_QUOTA_EXHAUSTED`; the current code reports a Qrypt 403 as `QRYPT_FORBIDDEN` (D41).
- **Re-run on build `855e8d6`** (integrator, 2026-10-13): a submit at 12:08:39Z under the decoy token returned 503 `QRYPT_FORBIDDEN`, "Qrypt refused the request (403): the token is invalid or expired, or the entropy quota is used up.", and wrote no job. The test asserts that `GET /jobs` is unchanged, and the `qmc-dev-seeding-failures` alarm went OK → ALARM at 12:09:32Z and notified SNS ([TEST_RESULTS.md](TEST_RESULTS.md), "Fail-closed drill re-run after contract change 003").

## IAM, one role per function

Every policy is an `aws_iam_policy_document`. No action or resource contains `*`, with one exception: the `:*` suffix on each function's **own** log group ARN, which is how CloudWatch Logs names the log streams inside that group.

| Principal | Allowed actions | On |
| --- | --- | --- |
| API role `qmc-dev-api` | `dynamodb:GetItem`, `BatchGetItem`, `PutItem`, `UpdateItem`, `Query` | the table ARN |
| | `sqs:SendMessage` | the shard queue ARN |
| | `ssm:GetParameter` | the token parameter ARN |
| | `lambda:InvokeFunction` (async self-invoke of the PENDING_ENTROPY seeder, contract change 008) | its **own** function ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | `<API log group ARN>:*` |
| | `states:StartExecution`, **only when `enable_step_functions = true`** (contract change 007) | the watcher state machine ARN |
| Worker role `qmc-dev-worker` | `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes`, `ChangeMessageVisibility` | the shard queue ARN |
| | `dynamodb:PutItem`, `UpdateItem`, `Query` | the table ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | `<worker log group ARN>:*` |
| Watcher role `qmc-dev-watcher`, **only when `enable_step_functions = true`** | `dynamodb:GetItem` | the table ARN |
| Client role `qmc-dev-client`, grader user `qmc-dev-grader` | `lambda:InvokeFunctionUrl` if `lambda:FunctionUrlAuthType = AWS_IAM`; `lambda:InvokeFunction` if `lambda:InvokedViaFunctionUrl = true` | the API function ARN only |

What is deliberately absent, and why:

- **No `kms:Decrypt`.** The token is encrypted with the AWS-managed `aws/ssm` key, whose policy lets account principals decrypt through SSM. The first live submit decrypted it with `ssm:GetParameter` alone (D39).
- **No `cloudwatch:PutMetricData`.** Its only valid resource is `*`, so metrics are written as EMF log lines instead (D11).
- **No X-Ray.** Its write actions also need `*` (D18).
- **No `logs:CreateLogGroup`.** Terraform creates every log group before its function (R11).

Both Lambda trust policies require `aws:SourceAccount` to be this account. The watcher role, when created, trusts `states.amazonaws.com` with `aws:SourceAccount` and `aws:SourceArn` set to the watcher's own ARN. The client role trusts only this account's root, so only identities in this account that are allowed to assume it can use it.

**Checked on the deployed stack** (2026-10-13, build `855e8d6`, read-only IAM calls `GetRole`, `ListRolePolicies` / `GetRolePolicy`, `ListAttachedRolePolicies`, `ListUserPolicies` / `GetUserPolicy`, `ListAttachedUserPolicies`, `ListGroupsForUser`, `ListAccessKeys`):

- Each of `qmc-dev-api`, `qmc-dev-worker`, `qmc-dev-client` and `qmc-dev-grader` has exactly one inline policy, and it matches its Terraform policy document statement for statement: the same actions, resources and conditions as the table above.
- None of the four has an attached managed policy, and the grader belongs to no group.
- The two Lambda trust policies allow `lambda.amazonaws.com` with `aws:SourceAccount` set to this account. The client role trusts only this account's root.
- The grader user had **0 access keys** at the time of the check, as intended: its key is created last (D26).
- **Re-checked after contract changes 007 and 008** (`main` `581563d`, applied 18:09Z; `GetRolePolicy`, `GetFunctionEventInvokeConfig`, `ListStateMachines`): the API policy has exactly the five statements `Table`, `ShardQueue`, `QryptToken`, `SelfInvoke` (`lambda:InvokeFunction` on `qmc-dev-api` only) and `Logs`. There is no `StartWatcher` statement and no state machine, because the watcher is off in `dev`. The self-invoke's event-invoke config has `MaximumRetryAttempts` 0 and `MaximumEventAgeInSeconds` 300, so Lambda never retries a seeder event; the seeder's own attempt limit (`PENDING_MAX_ATTEMPTS` 4) bounds the loop.
- The fail-closed drill temporarily pointed the API's `ssm:GetParameter` grant and `QRYPT_API_KEY_PARAM` at the decoy parameter, from 12:08:28Z until the revert at 12:09:10Z. The check above ran before the drill. At 12:09:29Z the `QryptToken` statement and `QRYPT_API_KEY_PARAM` were confirmed back on `/qmc/qrypt-api-key`.

## Function URL auth and the grader user

- The API's Function URL uses `authorization_type = "AWS_IAM"`, so every request must be SigV4-signed by an identity holding both invoke permissions above. Since October 2025 a Function URL needs both (R9). Anonymous submission is impossible.
- Graders use the IAM user `qmc-dev-grader`. Its only permission is the invoke policy above, attached inline on the user. The project's managed service control policy `DenyIAMRestrictedActions` denies IAM groups, so Checkov `CKV_AWS_40` is skipped with that reason (D37).
- The grader's access key is created with the AWS CLI **after the last apply** and sent privately, so it never enters Terraform state or the repo (D26). It will be deleted after grading.
- The grader user has `force_destroy = false`. The project SCP denies `iam:*LoginProfile*`, so a forced destroy fails. Its access key is therefore deleted by CLI before any destroy (D44, HANDOFF R25).

## Encryption

| Data | At rest | Key |
| --- | --- | --- |
| DynamoDB table | Encrypted | AWS-owned key (no `server_side_encryption` block) |
| SQS shard queue and DLQ | SSE-SQS | SQS-managed key |
| SSM token parameter | SecureString | AWS-managed `aws/ssm` |
| CloudWatch Logs | Encrypted by default | service-managed |
| SNS alerts topic | **Unencrypted** | AWS Budgets cannot publish to a topic encrypted with the AWS-managed SNS key (D17). The topic carries alarm and budget notices, never job data or secrets |

**No customer-managed KMS keys:** `aws_kms_key` does not appear in `infra/`. Each such key costs $1 a month, which is the whole Budget (I7). The assignment allows AWS-owned or AWS-managed keys.

**In transit:** both queues have a policy that denies every `sqs:*` call where `aws:SecureTransport` is false. The policy uses `Principal: "*"` inside a **Deny**, which is the standard pattern and grants nothing. The Function URL, Qrypt, DynamoDB, SSM and every other AWS endpoint are HTTPS only.

## Logs and redaction

- Every log line is one JSON object written to stdout by `qmc_core.logjson`. A redaction filter drops any key whose name contains `authorization`, `token`, `api_key`, `apikey`, `secret`, `block` or `password` (`src/qmc_core/logjson.py`). The API never logs request headers.
- Each Qrypt attempt logs a `qrypt_call` event with the HTTP status and latency, never the token or the entropy block (HANDOFF §9.3).
- The block is not stored. Only its SHA-256 is kept, which lets the API refuse a block it has already used.
- Log groups keep 14 days.

**Log scan** (WS4, `tools/log_scan.py`, 2026-10-13 about 12:10Z, after both fail-closed drills; [TEST_RESULTS.md](TEST_RESULTS.md), "Secret-leak scan of the logs"):
- It read all 1,733 events since 00:00 UTC in `/aws/lambda/qmc-dev-api` and `/aws/lambda/qmc-dev-worker`. It printed counts only, never text.
- **0 matches** for `Bearer`, `authorization` (any case), `api_key`, `"token"` and the decoy marker `NOT-A-REAL-TOKEN`, which the API held in memory during both drills.
- A control pattern (`qrypt_call`, 29 matches in the API group) proves the scan read log text.
- It deliberately does not search for the real token's prefix, because that would mean reading the secret.

## Fault injection on the graded stack

The deployed stack has fault injection on (`FAULT_INJECTION_ENABLED=true` on `qmc-dev-api`, read with `lambda:GetFunctionConfiguration` on build `855e8d6`; D28). This is deliberate: it lets a grader reproduce the forced-failure demo. A request with `fault_injection.fail_shards` makes the listed shards fail, the job ends PARTIAL, the messages reach the DLQ and the alarm emails.

What a caller can do with it:

- Only an authenticated caller (the grader user or an account identity) can use it.
- It affects only that caller's own job.
- The listed indexes are validated like every other field.
- It writes nothing beyond the normal job and shard items.

With the Terraform default (`false`), the field is rejected with `FAULT_INJECTION_DISABLED`.

## Checkov suppressions

Checkov is a **blocking** job in both CI systems: GitLab CI and GitHub Actions run `checkov -d infra` (3.3.28) with no `allow_failure` or `continue-on-error`, so a new finding fails the pipeline and, because `main` is protected, blocks the merge (QMC-119, MR !37; D42). Re-run on `main` `581563d` (after contract changes 007 and 008): **140 passed, 0 failed, 22 skipped**. The 22 skips cover 15 checks. Each is suppressed inline with the reason below, quoted from `infra/modules/*/main.tf`.

| Check | Resources | Reason |
| --- | --- | --- |
| `CKV_AWS_26` SNS encryption | alerts topic | Budgets cannot publish to a topic using the AWS-managed SNS key, and a CMK costs $1/month (D17) |
| `CKV_AWS_28` DynamoDB PITR | table | PITR is billed per GB-month with no free tier (I7); every result is reproducible by replay |
| `CKV_AWS_40` IAM policy on a user | grader user policy | IAM groups are denied by the project's managed SCP; the user has only this invoke policy (D37) |
| `CKV_AWS_50` X-Ray tracing | both Lambdas | X-Ray write actions need `Resource "*"` (D18) |
| `CKV_AWS_115` reserved concurrency | API Lambda | Reserved concurrency 0 disables the Function URL; submits are capped by the Qrypt limiter instead. The worker passes (D12, D40) |
| `CKV_AWS_116` Lambda DLQ | API Lambda | Invoked synchronously by the Function URL; an async DLQ never applies |
| | worker Lambda | Invoked by the SQS event source mapping; failed messages go to the queue redrive DLQ, which is alarmed |
| `CKV_AWS_117` Lambda in a VPC | both Lambdas | No private resources to reach; a VPC needs NAT or endpoints, which are not free |
| `CKV_AWS_119` DynamoDB CMK | table | A customer-managed KMS key costs $1/month (I7); the AWS-owned key encrypts at rest |
| `CKV_AWS_158` log group CMK | both log groups | A customer-managed KMS key costs $1/month (I7); CloudWatch Logs encrypts at rest by default |
| `CKV_AWS_173` Lambda env encryption | both Lambdas | No secrets in the environment (the Qrypt token stays in SSM); default AWS-managed encryption; a CMK costs $1/month |
| `CKV_AWS_272` code signing | both Lambdas | Code signing needs S3 and a signing profile; zips are deterministic and hashed in `build/manifest.json` |
| `CKV_AWS_284` Step Functions X-Ray tracing | watcher state machine (only when enabled) | X-Ray tracing off (D18); the machine only polls one item |
| `CKV_AWS_285` Step Functions execution logging | watcher state machine (only when enabled) | Execution logging off by design (007): no log group, no ingestion cost; execution history is kept by Step Functions |
| `CKV_AWS_338` log retention ≥ 1 year | both log groups | 14-day retention by design (I7, HANDOFF §9.3); results live in DynamoDB, not logs |
| `CKV2_AWS_16` DynamoDB autoscaling | table | Fixed 10/10 provisioned capacity is the free tier (D7); autoscaling could leave it |
