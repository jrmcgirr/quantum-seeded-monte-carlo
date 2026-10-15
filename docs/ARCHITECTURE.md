# Architecture

> Drawn from HANDOFF §8 (QMC-150), then reconciled with `terraform state list` from the fresh-clone rehearsal (QMC-152, 2026-10-15, `main` `a5b9ec0`): every one of the 37 managed resources appears below.

All resources are in **us-east-2** and named `qmc-<env>` (`env` = `dev`), except the account-wide Budget `qmc-monthly`.

## Resources and data flow

```mermaid
flowchart LR
    subgraph clients["Callers"]
        CLI["clients/qmc.py or curl (SigV4)"]
        GU["IAM user qmc-env-grader (inline invoke policy, D37)"]
        CR["IAM role + policy qmc-env-client"]
    end

    subgraph api["module api"]
        URL["Lambda Function URL (AWS_IAM, BUFFERED)"]
        APIF["Lambda qmc-env-api (python3.13, x86_64, 512 MB)"]
        APIR["IAM role + policy: api"]
        APIL["Log group /aws/lambda/qmc-env-api (14 d)"]
        EIC["Event-invoke config: 0 retries, max age 300 s (008)"]
        SFN["Optional: Step Functions qmc-env-watcher + role and policy (off by default, on in the deployed stack, 007)"]
    end

    subgraph ext["Outside Terraform"]
        SSM["SSM SecureString /qmc/qrypt-api-key (aws/ssm key, created by CLI)"]
        QR["Qrypt api-eus.qrypt.com"]
    end

    subgraph data["module data"]
        DDB[("DynamoDB qmc-env (provisioned 10/10, TTL)")]
        SQ["SQS qmc-env-shards (SSE-SQS)"]
        DLQ["SQS qmc-env-shards-dlq (SSE-SQS, 14 d)"]
        QP["Queue policies: deny non-TLS; redrive policy: 3 receives"]
    end

    subgraph workers["module workers"]
        ESM["Event source mapping (batch 1, max concurrency 5)"]
        WF["Lambda qmc-env-worker (python3.13, x86_64, 1,024 MB, NumPy 2.5.4, exp on X86_V3)"]
        WR["IAM role + policy: worker"]
        WL["Log group /aws/lambda/qmc-env-worker (14 d)"]
        RMC["Runtime management config: FunctionUpdate (D38)"]
    end

    subgraph obs["module observability + budget"]
        ALM["CloudWatch alarms (7)"]
        DASH["CloudWatch dashboard qmc-env"]
        SNS["SNS qmc-env-alerts + topic policy"]
        SUB["Email subscription"]
        BUD["AWS Budget qmc-monthly ($1)"]
    end

    CLI --> URL
    GU -. invoke-only .-> URL
    CR -. invoke-only .-> URL
    URL --> APIF
    APIR -.- APIF
    APIF -- "GetParameter (cached 300 s)" --> SSM
    APIF -- "GET 1 KiB, 2.5 s deadline" --> QR
    APIF -- "limiter, entropy claim, job, joblist" --> DDB
    APIF -- "SendMessageBatch, 1 per shard" --> SQ
    APIF -- "stdout JSON + EMF" --> APIL
    APIF -- "async self-invoke: PENDING_ENTROPY seeder (opt-in, ≤ 4 attempts)" --> APIF
    EIC -.- APIF
    APIF -. "StartExecution (only if enabled)" .-> SFN
    SFN -. "GetItem every 10 s until terminal" .-> DDB
    QP -.- SQ
    QP -.- DLQ
    SQ --> ESM --> WF
    WR -.- WF
    RMC -.- WF
    WF -- "shard put, done set, finalize" --> DDB
    WF -- "stdout JSON + EMF" --> WL
    SQ -- "after 3 receives" --> DLQ
    APIL -- "EMF metrics QMC/env" --> ALM
    WL -- "EMF metrics QMC/env" --> ALM
    DLQ -- "depth > 0" --> ALM
    ALM --> SNS --> SUB
    BUD --> SNS
    ALM -.- DASH
    style SFN stroke-dasharray: 5 5
```

Dashed outline: the Step Functions watcher and its role exist only with `enable_step_functions = true` (contract change 007). WS4's drill switched it on (2026-10-13 18:16Z): a COMPLETE job's execution SUCCEEDED and a PARTIAL job's FAILED with `JobNotComplete` (docs/TEST_RESULTS.md). It is **on in the final deployed stack**: at the rehearsal both executions, named by job id (`0aaa055f…` and its replay `9dbf6846…`), SUCCEEDED in 10.2 s (`ListExecutions`, 16:11:51–16:12:05Z).

### Reconciled with `terraform state list`

The rehearsal's state list has 52 entries: **37 managed resources**, matching "37 added" from the apply, and 15 data sources.

| Module | Managed resources in state | In the diagram as |
| --- | --- | --- |
| `api` (13) | `aws_lambda_function.api`, `aws_lambda_function_url.api`, `aws_lambda_function_event_invoke_config.api`, `aws_cloudwatch_log_group.api`, `aws_iam_role.api` + `aws_iam_role_policy.api`, `aws_iam_role.client` + `aws_iam_role_policy.client`, `aws_iam_user.grader[0]` + `aws_iam_user_policy.grader[0]`, `aws_sfn_state_machine.watcher[0]`, `aws_iam_role.watcher[0]` + `aws_iam_role_policy.watcher[0]` | URL, APIF, EIC, APIL, APIR, CR, GU, SFN |
| `data` (6) | `aws_dynamodb_table.main`, `aws_sqs_queue.shards`, `aws_sqs_queue.dlq`, `aws_sqs_queue_policy.shards`, `aws_sqs_queue_policy.dlq`, `aws_sqs_queue_redrive_policy.shards` | DDB, SQ, DLQ, QP |
| `workers` (6) | `aws_lambda_function.worker`, `aws_lambda_event_source_mapping.shards`, `aws_lambda_runtime_management_config.worker`, `aws_cloudwatch_log_group.worker`, `aws_iam_role.worker` + `aws_iam_role_policy.worker` | WF, ESM, RMC, WL, WR |
| `observability` (11) | `aws_cloudwatch_metric_alarm.this` × 7 (`api-errors`, `dlq-depth`, `jobs-partial`, `queue-age`, `seeding-failures`, `worker-errors`, `worker-throttles`), `aws_cloudwatch_dashboard.main`, `aws_sns_topic.alerts`, `aws_sns_topic_policy.alerts`, `aws_sns_topic_subscription.email` | ALM, DASH, SNS, SUB |
| `budget[0]` (1) | `aws_budgets_budget.monthly` | BUD |

The 15 data sources create nothing: `aws_caller_identity` (×2), `aws_region` (×2), and 11 `aws_iam_policy_document`s that render the role, user, queue and topic policies above (`api`, `assume`, `client_assume`, `invoke`, `watcher[0]`, `watcher_assume[0]`, `tls_only["shards"]`, `tls_only["dlq"]`, `alerts`, and the worker's `assume` and `worker`). There is no `aws_lambda_permission`: with `AWS_IAM` auth, the callers' identity policies grant `lambda:InvokeFunctionUrl`. The dashboard was built, not cut. Outside Terraform: the SSM parameter (created by CLI, I6) and the grader's access key (created by CLI, D44).

## Submit, including the fail-closed branch

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API Lambda
    participant D as DynamoDB
    participant S as SSM
    participant Q as Qrypt
    participant M as SQS shards
    C->>A: POST /jobs (SigV4)
    A->>A: validate (400 on any rule, nothing written)
    A->>D: Qrypt gap slot RL qrypt LAST (429 if under 2 s since the last call)
    A->>S: GetParameter token (cached)
    A->>Q: GET quantum-entropy size=1
    alt Qrypt or secret failure
        A-->>C: 4xx/5xx naming the cause, SeedingFailures=1, no job, no message
    else 1 KiB block
        A->>D: entropy claim (block never reused)
        A->>A: HKDF-SHA256 master seed
        A->>D: job put (QUEUED), joblist put
        A->>M: one message per shard
        A-->>C: 202 job_id, master_seed_hex
    end
```

## Opt-in PENDING_ENTROPY (contract change 008)

A submit with `"on_entropy_failure": "queue"` that hits a **retryable** entropy failure (our 429 `ENTROPY_RATE_LIMITED`, a Qrypt 429 or 5xx, a timeout or a network error) is parked instead of rejected, if fewer than `MAX_PENDING_ENTROPY` (10) jobs are pending. Token, quota and bad-response failures still reject at once, writing nothing.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API Lambda (request)
    participant S as API Lambda (seeder, async)
    participant D as DynamoDB
    participant Q as Qrypt
    participant M as SQS shards
    C->>A: POST /jobs, on_entropy_failure "queue"
    A->>Q: entropy (retryable failure)
    A->>D: job put PENDING_ENTROPY (params only, no seed), joblist put
    A->>S: Invoke, InvocationType Event, attempt 1
    A-->>C: 202 job_id, status PENDING_ENTROPY
    loop attempt 1..4 (PENDING_MAX_ATTEMPTS), waits 2, 4, 6, 6 s
        S->>D: Qrypt gap slot
        opt slot refused (not an attempt)
            S->>S: wait Retry-After + 0–2 s jitter, in process or by a self-invoke hop (≤ 15)
        end
        S->>Q: GET 1 KiB
        alt block received
            S->>D: entropy claim, then PENDING_ENTROPY → QUEUED with seed (conditional)
            S->>M: one message per shard
        else retryable failure
            S->>D: entropy_attempts + 1 (conditional on PENDING_ENTROPY)
            S->>S: Invoke, attempt + 1
        else non-retryable, or last attempt
            S->>D: PENDING_ENTROPY → FAILED, naming the cause
        end
    end
    C->>A: GET /jobs/{id} past pending_until (120 s)
    A->>D: PENDING_ENTROPY → FAILED (backstop for a lost async event)
```

Every transition is conditional on `status = PENDING_ENTROPY`, so the seeder and the GET backstop cannot both win. Lambda never retries the async event (event-invoke config: 0 retries, dropped after 300 s). A refused limiter slot does not count as an attempt (MR !57). Without that rule, seeders woken together lost the slot to each other and 5 of 10 jobs in WS4's first burst failed. With it, all 10 completed (docs/TEST_RESULTS.md).

## Shard processing and finalize

```mermaid
sequenceDiagram
    autonumber
    participant M as SQS shards
    participant W as Worker Lambda
    participant D as DynamoDB
    participant L as DLQ
    M->>W: one message (batch 1, ≤ 5 concurrent)
    W->>W: decode, then simulate shard i from (master seed, i)
    alt success
        W->>D: shard put SHARD#i (conditional: first write wins)
        Note over W,D: a duplicate delivery is refused here and continues
        W->>D: ADD i to done_shards (conditional: the job exists)
        opt every shard now done or failed
            W->>D: query all shards, aggregate (fsum)
            W->>D: COMPLETE, or PARTIAL with failed indexes and a partial result (conditional on status)
        end
    else failure (attempt < 3)
        W->>M: visibility 5 s, report batch item failure
    else failure on attempt 3
        W->>D: ADD i to failed_shards (conditional: i not already done), then finalize as above
        W->>M: report failure
        M->>L: 4th receive moves it to the DLQ (depth alarm)
    end
```

The worker never raises: it returns `batchItemFailures`, so only the failed message is retried. Each finalize write is conditional; if another worker finalized first, it re-decides once from the returned item and stops. A message that cannot be decoded has no job to mark and goes to the DLQ after 3 receives (HANDOFF §7.6).

## Replay

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API Lambda
    participant D as DynamoDB
    participant M as SQS shards
    participant W as Worker Lambda
    C->>A: POST /jobs/{id}/replay
    A->>D: get original (404 if missing, 409 unless COMPLETE)
    A->>D: new job with the original's seed, params, sharding (no Qrypt call)
    A->>M: one message per shard
    A-->>C: 202 new job_id, replay_of
    M->>W: shards
    W->>D: shard results + fingerprint
    C->>A: GET /jobs/{new id}
    A->>D: both jobs' shards
    A-->>C: replay_check: bit_identical, shard_diffs, fingerprint_match
```
