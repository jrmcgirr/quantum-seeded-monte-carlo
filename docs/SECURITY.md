# Security

Checked against `infra/` on `main` `855e8d6`, the deployed API Lambda's configuration, and the live drills in [TEST_RESULTS.md](TEST_RESULTS.md). *TBD* marks what is still to be verified before G4.

## The Qrypt token's path

1. **Created out-of-band.** James stores the token as an SSM **SecureString** `/qmc/qrypt-api-key` with the AWS CLI (README step 2; `read -s` keeps it out of shell history and `ps`). It is never a Terraform variable, a tfvars value, an environment variable or a file in the repo.
2. **Terraform knows only the name.** The parameter ARN is built from the Region, the account id and `var.qrypt_api_key_param_name`. That is all the API role's `ssm:GetParameter` grant needs.
3. **Fetched at runtime.** The API Lambda calls `GetParameter(WithDecryption=True)` and caches the value in memory for `SECRET_CACHE_TTL_S` (300 s on the deployed function). It sends the token only to Qrypt, over TLS, in an `Authorization: Bearer` header.

**Why out-of-band:** anything Terraform reads or writes lands in its state file in plain text. The trap is `data "aws_ssm_parameter"`, which looks harmless but stores the **decrypted** value in state. It is banned (HANDOFF §8.1, R10), and CI's `ssm-data-source-guard` job fails the pipeline if one appears. A search of `infra/` on `855e8d6` finds none.

**If the token fails, nothing is written (I1).** Two live checks, both in TEST_RESULTS:

- **Secret failure** (parameter missing): 503 `SECRET_UNAVAILABLE`, no job written.
- **Decoy-token drill** (a wrong token stored under `/qmc/qrypt-api-key-invalid`, build `d0af0b1`): 503, no job written, and the `qmc-dev-seeding-failures` alarm went to ALARM. This drill ran before contract change 003, so it reported `QRYPT_QUOTA_EXHAUSTED`; the current code reports a Qrypt 403 as `QRYPT_FORBIDDEN` (D41). *TBD: cite the re-run with `QRYPT_FORBIDDEN`.*

## IAM, one role per function

Every policy is an `aws_iam_policy_document`. No action or resource contains `*`, with one exception: the `:*` suffix on each function's **own** log group ARN, which is how CloudWatch Logs names the log streams inside that group.

| Principal | Allowed actions | On |
| --- | --- | --- |
| API role `qmc-dev-api` | `dynamodb:GetItem`, `BatchGetItem`, `PutItem`, `UpdateItem`, `Query` | the table ARN |
| | `sqs:SendMessage` | the shard queue ARN |
| | `ssm:GetParameter` | the token parameter ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | `<API log group ARN>:*` |
| Worker role `qmc-dev-worker` | `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes`, `ChangeMessageVisibility` | the shard queue ARN |
| | `dynamodb:PutItem`, `UpdateItem`, `Query` | the table ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | `<worker log group ARN>:*` |
| Client role `qmc-dev-client`, grader user `qmc-dev-grader` | `lambda:InvokeFunctionUrl` if `lambda:FunctionUrlAuthType = AWS_IAM`; `lambda:InvokeFunction` if `lambda:InvokedViaFunctionUrl = true` | the API function ARN only |

What is deliberately absent, and why:

- **No `kms:Decrypt`.** The token is encrypted with the AWS-managed `aws/ssm` key, whose policy lets account principals decrypt through SSM. The first live submit decrypted it with `ssm:GetParameter` alone (D39).
- **No `cloudwatch:PutMetricData`.** Its only valid resource is `*`, so metrics are written as EMF log lines instead (D11).
- **No X-Ray.** Its write actions also need `*` (D18).
- **No `logs:CreateLogGroup`.** Terraform creates every log group before its function (R11).

Both Lambda trust policies require `aws:SourceAccount` to be this account. The client role trusts only this account's root, so only identities in this account that are allowed to assume it can use it.

**Checked on the deployed stack** (2026-10-13, build `855e8d6`, read-only IAM calls `GetRole`, `ListRolePolicies` / `GetRolePolicy`, `ListAttachedRolePolicies`, `ListUserPolicies` / `GetUserPolicy`, `ListAttachedUserPolicies`, `ListGroupsForUser`, `ListAccessKeys`):

- Each of `qmc-dev-api`, `qmc-dev-worker`, `qmc-dev-client` and `qmc-dev-grader` has exactly one inline policy, and it matches its Terraform policy document statement for statement: the same actions, resources and conditions as the table above.
- None of the four has an attached managed policy, and the grader belongs to no group.
- The two Lambda trust policies allow `lambda.amazonaws.com` with `aws:SourceAccount` set to this account. The client role trusts only this account's root.
- The grader user had **0 access keys** at the time of the check, as intended: its key is created last (D26).

## Function URL auth and the grader user

- The API's Function URL uses `authorization_type = "AWS_IAM"`, so every request must be SigV4-signed by an identity holding both invoke permissions above. Since October 2025 a Function URL needs both (R9). Anonymous submission is impossible.
- Graders use the IAM user `qmc-dev-grader`. Its only permission is the invoke policy above, attached inline on the user. The project's managed service control policy `DenyIAMRestrictedActions` denies IAM groups, so Checkov `CKV_AWS_40` is skipped with that reason (D37).
- The grader's access key is created with the AWS CLI **after the last apply** and sent privately, so it never enters Terraform state or the repo (D26). It will be deleted after grading.

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

*TBD (WS4): a scan of both log groups for `Bearer`, `authorization`, `api_key` and `token` and for the decoy token's marker, finding nothing. The scan deliberately does not search for the real token's prefix, because that would mean reading the secret.*

## Fault injection on the graded stack

The deployed stack has fault injection on (`FAULT_INJECTION_ENABLED=true` on `qmc-dev-api`, read with `lambda:GetFunctionConfiguration` on build `855e8d6`; D28). This is deliberate: it lets a grader reproduce the forced-failure demo. A request with `fault_injection.fail_shards` makes the listed shards fail, the job ends PARTIAL, the messages reach the DLQ and the alarm emails.

What a caller can do with it:

- Only an authenticated caller (the grader user or an account identity) can use it.
- It affects only that caller's own job.
- The listed indexes are validated like every other field.
- It writes nothing beyond the normal job and shard items.

With the Terraform default (`false`), the field is rejected with `FAULT_INJECTION_DISABLED`.

## Checkov suppressions

`checkov -d infra` (Checkov 3.3.28, `main` `855e8d6`) gives **137 passed, 0 failed, 20 skipped**. The 20 skips cover 13 checks. Each is suppressed inline with the reason below, quoted from `infra/modules/*/main.tf`.

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
| `CKV_AWS_338` log retention ≥ 1 year | both log groups | 14-day retention by design (I7, HANDOFF §9.3); results live in DynamoDB, not logs |
| `CKV2_AWS_16` DynamoDB autoscaling | table | Fixed 10/10 provisioned capacity is the free tier (D7); autoscaling could leave it |
