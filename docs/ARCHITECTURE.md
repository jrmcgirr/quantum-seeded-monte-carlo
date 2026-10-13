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
```

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
