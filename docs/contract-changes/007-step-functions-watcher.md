# 007 Optional Step Functions watcher (QMC-127, #53)

- **Requested by:** James (stretch scoping), 2026-10-13
- **Status:** accepted 2026-10-13
- **Affects:** WS0 (HANDOFF D21, §8.4, §10, I7 wording), WS1 (state machine, IAM, toggle), WS2 (StartExecution), WS4 (validation, drill), WS5 (ARCHITECTURE, COST, DECISIONS)

## Change

- Terraform root variable `enable_step_functions` (bool, default **false**). When true: `aws_sfn_state_machine` `qmc-<env>-watcher` (STANDARD, logging OFF, tracing off), execution role trusted by `states.amazonaws.com` (conditions `aws:SourceAccount`, `aws:SourceArn`) with `dynamodb:GetItem` on the table ARN only; the API role gains `states:StartExecution` on the machine ARN only; output `state_machine_arn` (null when off).
- API env `SFN_ARN` (unset or empty = feature off).
- Definition: `Wait 10 s → GetItem(pk = JOB#<id>, sk = META, ConsistentRead) with Retry [States.TaskFailed, DynamoDB.ProvisionedThroughputExceededException; 2 s, ×2, max 3] → Choice on status: QUEUED or PENDING_ENTROPY → Wait; COMPLETE → Succeed; PARTIAL or FAILED → Fail`; `TimeoutSeconds` 1,200 (covers PENDING_MAX_AGE_S + JOB_DEADLINE_S).
- The API calls `StartExecution(stateMachineArn=SFN_ARN, name=<job_id>, input={"job_id": <id>})` after a job reaches `QUEUED` (submit, replay, PENDING seeding). Any error is logged as `sfn_start_failed` and never changes the HTTP response. `ExecutionAlreadyExists` is success.
- No new metric, alarm or log group. Off by default, so the $0 claim is unchanged; on, it is free up to ~700 jobs/month (4,000 always-free transitions; ~4–7 per job).

## Impact on invariants

None: the machine only reads. I7 holds by default.
