# Security

> **Skeleton (QMC-150).** Filled in at QMC-154 from the deployed IAM policies and WS1's Checkov suppressions.

## The Qrypt token's path

*TBD: out-of-band SSM SecureString → runtime fetch → never in state, the repo or the logs; the `data "aws_ssm_parameter"` pitfall.*

## IAM, one role per function

*TBD: the policy table from the deployed stack; the `:*` log-stream suffix; `kms:Decrypt` outcome.*

## Function URL auth and the grader user

*TBD*

## Encryption

*TBD: at rest (SSE-SQS, DynamoDB AWS-owned key, `aws/ssm`), why no customer-managed keys, the unencrypted alerts topic; TLS-only queue policies.*

## Logs and redaction

*TBD*

## Fault injection on the graded stack

*TBD: left enabled deliberately, and what it allows.*

## Checkov suppressions

*TBD: each suppression with its reason (from WS1).*
