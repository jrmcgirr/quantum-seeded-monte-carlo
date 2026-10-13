# Quantum-Seeded Monte Carlo Platform on AWS

AI Accelerated Development Â· Role: Cloud Engineer

## Difficulty & Timebox

Tier: Mid Â· Timebox: 2 days Â· Format: take-home assessment
Target industry: Financial services (risk and derivatives pricing)

**Before the timebox starts** (not counted in the 2 days): have an AWS account
you can deploy to with billing alerts enabled, a working Qrypt access token
(see Qrypt Quick Reference), and Terraform and the AWS CLI installed and
authenticated. New AWS account verification can take hours, so do this first.

## Problem Statement

Quantitative teams run Monte Carlo simulations to price options and estimate
risk. Two things routinely go wrong: the random seeds are weak or reused (so
"independent" runs are correlated), and the infrastructure is hand-built in a
console, so nobody can reproduce a run or explain its bill.

Build a serverless, fully Terraform-managed Monte Carlo platform on AWS that:

- seeds every job from **Qrypt quantum entropy**,
- fans each job out across parallel workers with independent derived seeds,
- aggregates the result with a confidence interval,
- can **replay** any past job bit-for-bit from its stored seed, and
- stays inside AWS always-free allowances with a budget alarm as a backstop.

The reference workload is pricing a **European call option** under
Black-Scholes, which has a closed-form answer so correctness is checkable.

Target users: **quant analysts** (submit jobs, read results), **platform
operators** (deploy, monitor, control cost), and **auditors** (replay a past run
and confirm its result).

## Business Context

Model-risk and audit functions require that a reported number can be
reproduced. A simulation platform that records seed provenance and replays runs
exactly removes days of back-and-forth during model validation. Running it
serverless with infrastructure-as-code means idle cost is zero, every
environment is identical, and spend is attributable per job.

## Illustrative Business Case / Impact Metrics

Illustrative. Assumptions stated inline; validate against real analytics.

- **Idle cost:** $0/month at rest versus an always-on compute node
  (assumption: one small on-demand instance â‰ˆ $15/month = **$180/year** avoided
  per environment).
- **Reproduction time:** cut "reproduce last quarter's run for the auditor" from
  ~2 days to < 5 minutes via the replay endpoint.
- **Environment setup:** from ~1 day of console work to one `terraform apply`
  (< 10 minutes) for a new, identical environment.

## Qrypt Quick Reference

Everything you need about the Qrypt service is in this section. Official docs:
https://docs.qrypt.com/eaas/

- **What it is:** Qrypt Quantum Entropy is a REST service that returns random
  bytes generated from quantum measurements.
- **Access token:** create an account at https://portal.qrypt.com/register and
  generate an access token for the **Entropy** service. If self-registration is
  not available to you, request an assessment token from your program contact.
  Locally, store it as `QRYPT_API_KEY` in a `.env` file. Never commit it.
- **Endpoint:** `GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size={n}`
  where `n` is the number of **KiB** (1,024-byte blocks) to return, **1 to 512**.
- **Headers:** `Accept: application/json` and
  `Authorization: Bearer {QRYPT_API_KEY}`
- **Response:**
  ```json
  { "random": ["<base64 of 1,024 bytes>", "<base64 of 1,024 bytes>"], "size": 2 }
  ```
- **Errors:**

  | Status | Meaning |
  |--------|---------|
  | 400 | Malformed request (e.g. `size` out of range) |
  | 401 | Token invalid or expired |
  | 403 | Account has used its entropy allotment for the current period |
  | 429 | Rate limit: more than **30 requests per 10 seconds** for this token |
  | 500 | Qrypt internal error |
  | 503 | Entropy supply temporarily insufficient |

- **Example:**
  ```bash
  curl -s -H "Accept: application/json" \
       -H "Authorization: Bearer $QRYPT_API_KEY" \
       "https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1"
  ```
- **No SDK required.** Call the REST endpoint directly from any language.
- **Assessment budget:** keep total live usage **â‰¤ 10 MB (10,240 KiB)** across
  the whole assessment. Your account's allotment may be lower; a 403 means you
  have hit it. One 1 KiB request per job is plenty (a 256-bit master seed needs
  32 bytes).

## AWS Free Tier Notes

AWS changed its Free Tier on 15 July 2025. New accounts now choose a Free plan
(credits, time-limited) or a Paid plan, and the old 12-month free EC2/RDS/S3
offers no longer apply to new accounts. **Always-free** monthly allowances still
apply to the services below. Confirm current figures at
https://aws.amazon.com/free before you start.

| Service | Always-free allowance (verify) | Use in this project |
|---------|-------------------------------|---------------------|
| Lambda | 1M requests + 400,000 GB-seconds / month | API, orchestrator, workers |
| DynamoDB | 25 GB storage + 25 provisioned RCU / 25 WCU | Jobs, shards, results |
| SQS | 1M requests / month | Shard fan-out + dead-letter queue |
| SNS | 1M publishes / month | Alarm notifications |
| CloudWatch | 10 custom metrics, 10 alarms | Job metrics, error and cost alarms |
| SSM Parameter Store (Standard) | No charge for standard parameters | `QRYPT_API_KEY` as a SecureString |

**Do not use:** NAT gateways, load balancers, EC2, RDS, or Secrets Manager
(none are always-free). Use **Lambda Function URLs** instead of API Gateway.
Use DynamoDB **provisioned** capacity within the free allowance, or on-demand
only if you can show the cost stays at $0. Use SQS **long polling**.

## Tech Stack

Language and tools are your choice within the constraints below. No paid
service required.

- **Infrastructure as code (required):** Terraform. One root module plus
  reusable modules; remote state is optional (local state is fine).
- **Function language (choose one):** Python, TypeScript/Node, Go, or Rust.
  Any dependencies must fit within Lambda package size limits.
- **AWS services:** as listed in AWS Free Tier Notes. Step Functions is allowed
  as a stretch (check its free state-transition allowance).
- **Dev tools:** Git, a test framework, `tflint` and `terraform validate`.
- **CI:** any free CI service.

## Functional Requirements

### Core (must-have)

1. **Job submission API.** A Lambda Function URL accepts
   `{S0, K, r, sigma, T, paths, shards}`, validates it, fetches one Qrypt
   entropy block, derives a 256-bit master seed, writes a job record to
   DynamoDB, and enqueues one SQS message per shard.
   Accept: `POST /jobs` returns a job id within the latency budget; the job
   record contains the master seed (hex), parameters, and status `QUEUED`.
2. **Independent derived seeds per shard.** Each worker derives its shard seed
   deterministically from the master seed and shard index (e.g.
   `SeedSequence(master).spawn(n)` or HKDF), never from time or a shared global
   RNG.
   Accept: a committed test shows shard seeds are distinct and that the same
   master seed always yields the same shard seeds.
3. **Fan-out workers and aggregation.** Worker Lambdas consume SQS, simulate
   their share of paths, and write `{sum, sum_sq, n}` per shard to DynamoDB. When
   all shards are complete, the job is aggregated into a price estimate,
   standard error, and 95% confidence interval.
   Accept: `GET /jobs/{id}` shows progress, then `COMPLETE` with the estimate and
   interval.
4. **Correctness against closed form.** For a standard test case
   (e.g. S0=100, K=100, r=0.05, Ïƒ=0.2, T=1), the estimate with 1,000,000 paths is
   within 3 standard errors of the Black-Scholes price.
   Accept: a committed test plus a deployed run demonstrate this.
5. **Deterministic replay.** `POST /jobs/{id}/replay` re-runs the job from its
   stored master seed and parameters.
   Accept: the replayed estimate is bit-identical to the original.
6. **Observability and cost guardrails.** Terraform creates: an AWS Budget
   (e.g. $1/month) with an email/SNS alert, CloudWatch custom metrics (jobs
   submitted, paths simulated, entropy KiB consumed, shard failures), and at
   least two alarms (worker errors, DLQ depth > 0).
   Accept: a forced worker failure trips the DLQ alarm and sends a notification.

### Stretch (bonus)

- **Step Functions orchestration** with retry and a wait-for-completion state.
- **CloudWatch dashboard** for job and entropy metrics.
- **Queued retry on entropy failure:** hold jobs as `PENDING_ENTROPY` and retry
  with backoff instead of rejecting them.
- **IaC security scan** (Checkov or tfsec) in CI with justified suppressions.
- **Portfolio Value-at-Risk** workload in addition to option pricing.
- **Ephemeral environments:** a GitHub Actions workflow that applies a
  per-branch stack and destroys it when the branch is deleted.
- **Per-job cost attribution:** estimated Lambda GB-seconds and request count
  per job, shown in the job result.

## Edge Cases & Failure Modes

Each item is an observable expectation a reviewer can check; a happy-path-only
submission should visibly fail here.

- **Qrypt unavailable at submit (401/403/429/500/503/timeout):** the job is
  rejected with a clear error naming the cause. It is **never** silently
  seeded from a local or time-based source.
- **Duplicate SQS delivery:** shard writes are idempotent (conditional put on
  `job_id + shard_index`); a redelivered message does not double-count paths.
- **Worker failure:** a shard that fails after the configured retry count lands
  in the DLQ; the job is marked `PARTIAL` with failed shard indexes, never
  reported as `COMPLETE` with a wrong total.
- **Lambda timeout:** shard size is bounded so a worker finishes well under its
  timeout; oversized `paths/shards` ratios are rejected at submit.
- **Burst of submissions:** concurrent job submissions do not exceed Qrypt's
  30 requests / 10 s limit (rate-limit in the submit path or queue requests).
- **Cost runaway:** Terraform sets reserved concurrency on workers and a
  maximum `paths` per job; a request above the limit is rejected.
- **Invalid inputs:** negative or zero `sigma`, `T`, `paths`; `shards` > paths;
  non-numeric fields: rejected with a 400 and a clear message.
- **Clean teardown:** `terraform destroy` removes every resource it created; a
  second `terraform plan` after apply shows no drift.
- **Empty / first-run:** `GET /jobs` on a fresh deployment returns an empty
  list, and `GET /jobs/{id}` for an unknown id returns a 404 with a clear
  message.

## Performance Benchmarks

| Target | Value | Measurement method |
|--------|-------|--------------------|
| Submit API latency | < 800 ms p95 (warm) | 50 submissions with a scripted client; report p50/p95 |
| Job wall-clock time | 1,000,000 paths in < 60 s | Timed from submit to `COMPLETE`, 5 runs |
| Steady-state cost | $0 at 100 jobs/day of 1M paths | Show the arithmetic against always-free allowances (requests, GB-seconds, DynamoDB, SQS) |
| Environment rebuild | `terraform apply` from empty < 10 min | Timed from a clean account/region |

## AI Metrics & Test Method

AI use here is primarily code generation assistance (Terraform, handlers,
tests). There is no user-facing AI feature and no AI accuracy metric to report.
Requirement: correctness is verified by committed tests, not by an AI model:

- price estimate within 3 standard errors of Black-Scholes,
- seed derivation determinism and shard-seed uniqueness,
- bit-identical replay,
- idempotent shard aggregation under duplicate delivery.

Any AI tooling used is recorded per AI Usage Disclosure below.

## Security, Privacy & Compliance

- **Secret handling:** `QRYPT_API_KEY` lives in SSM Parameter Store as a
  SecureString. It is created **out-of-band** (CLI or console), not passed as a
  Terraform variable, so it never lands in Terraform state. Document why.
- **Least-privilege IAM:** one role per function; each can touch only its own
  table/queue/parameter. No `*` actions or resources.
- **API auth:** Function URLs use `AWS_IAM` auth, or a bearer key validated
  against SSM. No anonymous job submission.
- **Encryption:** DynamoDB and SQS encryption at rest enabled (AWS-owned or
  AWS-managed keys); TLS in transit.
- **Logging:** structured logs with job id; no tokens in logs. Log retention set
  explicitly (e.g. 14 days) so log storage does not grow unbounded.
- **Input validation:** all inputs validated server-side.

## Code Quality & Engineering Practices

- Test coverage â‰¥ 80% on simulation math, seed derivation, aggregation, and
  input validation.
- Terraform split into modules (api, workers, data, observability, budget);
  variables and outputs documented; `terraform fmt` and `validate` clean.
- Clear separation of concerns: handlers are thin; simulation and aggregation
  are pure functions testable without AWS.
- Graceful degradation per Edge Cases; no unhandled exceptions in handlers.
- Health endpoint (`GET /health`) that checks DynamoDB and SSM reachability.
- CI runs unit tests, linter, `terraform validate`, and `tflint` on every push.

## AI Usage Disclosure

Required. Document in `AI_USAGE.md`: which AI tools you used, for what
(Terraform, codegen, tests, docs), and any prompts or configuration that
materially shaped the solution.

## Submission Requirements

- GitHub repository with README and `AI_USAGE.md`.
- `.env.example` for local runs; a mock Qrypt client so tests run without a
  token.
- Architecture diagram (committed image or Mermaid) showing every AWS resource
  and data flow.
- **Grader quick-start** in the README: configure AWS credentials, store the
  SSM parameter, `terraform apply`, submit a job, replay it, `terraform destroy`.
- Cost note: the free-tier arithmetic from Performance Benchmarks and a
  screenshot of the Budget configuration.
- Deployed endpoint URL (kept up through grading, then destroyed).
- A one-line project description (â‰¤ 120 characters) suitable for a project
  directory listing.
- Video demo (â‰¤ 5 min) showing: `terraform apply`, a job running to completion,
  the Black-Scholes comparison, a bit-identical replay, and the DLQ alarm firing.

## Evaluation Rubric (100 pts; pass â‰¥ 70)

| Criterion | Weight | Adequate â†’ Excellent |
|-----------|:------:|----------------------|
| Core functional requirements work | 30 | Single-Lambda run â†’ all 6 flawless incl. fan-out, replay, guardrails |
| Infrastructure as code quality | 20 | Partial console setup â†’ fully modular Terraform, clean destroy, no drift |
| Reliability & failure handling | 15 | Retries only â†’ idempotent shards, DLQ, `PARTIAL` status, fail-closed seeding |
| Security & IAM | 10 | Broad roles â†’ least privilege, secret out of state, no wildcard policies |
| Cost control & observability | 10 | No budget â†’ budget alarm, metrics, alarms, $0 arithmetic shown |
| Correctness tests | 5 | Ad-hoc â†’ committed closed-form, determinism, and idempotency tests |
| Docs, AI disclosure & demo | 10 | Sparse â†’ clear README, diagram, AI_USAGE, crisp demo |

