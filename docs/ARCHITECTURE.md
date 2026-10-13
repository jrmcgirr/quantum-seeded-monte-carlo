# Architecture

> **Draft (QMC-150),** drawn from HANDOFF §8 before anything is deployed. QMC-152 checks it against `terraform state list` after G2: every resource in state must appear here.

All resources are in **us-east-2** and named `qmc-<env>` (`env` = `dev`), except the account-wide Budget `qmc-monthly`.

## Resources and data flow

```mermaid
flowchart LR
    subgraph clients["Callers"]
        CLI["clients/qmc.py or curl (SigV4)"]
        GU["IAM user qmc-env-grader (inline invoke policy, D37)"]
        CR["IAM role qmc-env-client"]
    end

    subgraph api["module api"]
        URL["Lambda Function URL (AWS_IAM, BUFFERED)"]
        APIF["Lambda qmc-env-api (python3.13, x86_64, 512 MB)"]
        APIR["IAM role + policy: api"]
        APIL["Log group /aws/lambda/qmc-env-api (14 d)"]
        EIC["Event-invoke config: 0 retries, max age 300 s (008)"]
        SFN["Optional: Step Functions qmc-env-watcher + role (off by default, 007)"]
    end

    subgraph ext["Outside Terraform"]
        SSM["SSM SecureString /qmc/qrypt-api-key (aws/ssm key, created by CLI)"]
        QR["Qrypt api-eus.qrypt.com"]
    end

    subgraph data["module data"]
        DDB[("DynamoDB qmc-env (provisioned 10/10, TTL)")]
        SQ["SQS qmc-env-shards (SSE-SQS)"]
        DLQ["SQS qmc-env-shards-dlq (SSE-SQS, 14 d)"]
        QP["Queue policies: deny non-TLS"]
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
        SNS["SNS qmc-env-alerts"]
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

Dashed outline: the Step Functions watcher and its role exist only with `enable_step_functions = true` (contract change 007). WS4's drill switched it on (18:16Z): a COMPLETE job's execution SUCCEEDED and a PARTIAL job's FAILED with `JobNotComplete`. It was switched off again at 19:30Z, and `ListStateMachines` returns none (docs/TEST_RESULTS.md).

*TBD (QMC-152): reconcile with `terraform state list` (data sources, `aws_iam_policy_document`s, the redrive policy, the topic policy, the Function URL permission) and mark the dashboard as cut if QMC-118 is cut.*

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

*TBD (QMC-152): worker sequence: shard put (duplicate = continue), `ADD done_shards`, last shard aggregates and finalizes COMPLETE or PARTIAL; failure path to the DLQ.*

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
