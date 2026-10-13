# Decisions

Each entry gives what we chose, the alternatives, and why. The binding list is `docs/HANDOFF.md` section 3; the research behind each entry, with sources, is `docs/PRIMER.md`. D1–D21 were proposed by the research on 2026-10-12. James chose D1, D2, D5, D19 and D21 explicitly that evening and accepted the other recommended defaults, with concerns to be raised during review. D22–D32 came out of planning the same evening; D33–D35 and several refinements (marked *review*) came out of an independent review of the plan that night. Later entries are added during the build. WS5 owns this file; other workstreams send it entries.

## D1 Python 3.13 and NumPy

- **Chose:** Python 3.13 on the Lambda `python3.13` runtime (Amazon Linux 2023), NumPy 2.x pinned to one exact version with wheel hashes. *(James)*
- **Alternatives:** Go (pure-Go `math.Exp`, fast cold starts, but the compiler may fuse multiply-adds on arm64 and the seed spawning must be hand-rolled); Rust (strongest determinism, most build and coverage friction in two days); TypeScript/Node (no built-in seedable normal sampler).
- **Why:** NumPy's `SeedSequence` gives the per-shard seed derivation the assignment names, simulation costs about 25 ms per million paths, and pytest/coverage make the 80% gate cheap. The one weakness, CPU-dependent `exp`, is handled by D2.

## D2 x86_64 with pinned SIMD dispatch and a fail-closed self-check

- **Chose:** x86_64 Lambda; `NPY_DISABLE_CPU_FEATURES` set on the worker so NumPy never dispatches `exp` to AVX-512; a self-check at init that refuses to run if it did; a runtime fingerprint stored on every shard and compared by replay. *(James)*
- **Alternatives:** arm64 (one documented CPU family and about 20% cheaper, but untested and CI would need an arm64 runner or emulation for golden hashes); computing `exp` with `math.exp` over a list (avoids NumPy kernels, about 80 ms per million, depends on glibc).
- **Why:** a local experiment showed NumPy 2.x's AVX-512 `exp` differs from the baseline by 1 ULP in about 4.5% of elements, enough to change shard sums; Lambda's x86 fleet mixes Intel and AMD hosts. Pinning was the only mitigation verified experimentally, and CI runners are x86, so golden hashes run natively in the Lambda image.

## D3 HKDF-SHA256 master seed

- **Chose:** HKDF-SHA256 over the full 1 KiB Qrypt block, fixed public salt `qmc/hkdf-salt/v1`, `info = "qmc/master-seed/v1|" + job_id`, 32 bytes.
- **Alternatives:** the first 32 bytes of the block (assumes full entropy; Qrypt publishes no SP 800-90B assessment); SHA-256 of a tag and the block (fine, less explicit); mixing in `os.urandom` (would not weaken the seed, but breaks the "seeded only from Qrypt" provenance and could hide a Qrypt failure).
- **Why:** compressing 8,192 bits to 256 tolerates a large min-entropy shortfall; the salt and `info` give domain separation and versioning (RFC 5869).

## D4 SeedSequence spawn keys and PCG64DXSM

- **Chose:** `SeedSequence(master_int, spawn_key=(i,))` → `Generator(PCG64DXSM(...))`; default `pool_size`.
- **Alternatives:** `SeedSequence(master).spawn(n)` (identical seeds, but stateful and needs every index up front); HKDF-Expand per shard into a Philox key (language-neutral at the seed step, still tied to NumPy's normal sampler); `master + i` (explicitly unsafe per NumPy's guide).
- **Why:** NumPy-guaranteed independence, verified equal to `spawn()`, each worker needs only its index, and PCG64DXSM was the fastest generator measured.

## D5 Function URL with AWS_IAM

- **Chose:** `AWS_IAM` auth; callers' identity policies grant both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` (required for URLs created since October 2025); a SigV4 CLI client in the repo. *(James)*
- **Alternatives:** `NONE` plus a bearer key validated against SSM (plain curl in the demo, but a public resource policy, a second secret, constant-time comparison, header redaction and a scanner suppression).
- **Why:** no shared secret, callers attributable in CloudTrail, passes scanner checks. Consequence: graders need credentials (D26).

## D6 One API router Lambda and one worker

- **Chose:** a single API function routing on `rawPath` (`POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/replay`, `GET /health`) plus the worker.
- **Alternatives:** one function and URL per endpoint (tighter per-endpoint IAM, three times the Terraform, several hostnames).
- **Why:** a Function URL attaches to one function and gives no path parameters; one router keeps one URL, one role and one log group. If least privilege is challenged, the read-only status route can be split out in about an hour.

## D7 One provisioned DynamoDB table

- **Chose:** single table (`pk`, `sk`), PROVISIONED 10 RCU / 10 WCU, no autoscaling, no GSI, TTL, PITR off, AWS-owned encryption key.
- **Alternatives:** on-demand (no capacity planning, not free); separate jobs and shards tables (more readable, splits the free pool); a GSI for listing (consumes the shared pool).
- **Why:** only provisioned capacity is in the always-free tier (25/25 per account and region, shared by every table and index); 10/10 leaves room for a second stack; the 300 s burst bucket absorbs completion spikes.

## D8 Number-set fan-in

- **Chose:** conditional shard put, then `ADD done_shards :{i}` on the job, then a finalize conditioned on `status = QUEUED`; lazy finalize on `GET` as a repair path.
- **Alternatives:** an `ADD completed :1` counter (wrong under duplicates and crashes); `TransactWriteItems` (correct, about twice the write units, cannot return the new count); a DynamoDB Streams aggregator (extra function, `ListStreams` may need `*`); Step Functions (breaks the free tier at 100 jobs/day).
- **Why:** adding an element already in a set changes nothing, so retries repair rather than corrupt, at one write unit per step.

## D9 PARTIAL by last-attempt marking plus a deadline reaper

- **Chose:** the worker marks its shard failed when `ApproximateReceiveCount ≥ maxReceiveCount`; `GET` reaps jobs past their deadline; the DLQ is never consumed and is alarmed on `ApproximateNumberOfMessagesVisible > 0`; PARTIAL is final.
- **Alternatives:** alarm only (jobs stay QUEUED forever); a DLQ consumer (exact, but drains the DLQ so the alarm may never fire).
- **Why:** prompt for caught failures, complete for crashes and timeouts, and keeps the assignment's "forced failure trips the DLQ alarm" observable.

## D10 Fixed-window limiter, cap 15, fail fast

- **Chose:** a DynamoDB counter per aligned 10 s window with a conditional `ADD`, cap 15; over budget → 429 with `Retry-After`; at most one jittered retry of Qrypt 429/5xx/network within a 2.5 s deadline; never retry 400/401/403.
- **Alternatives:** cap 25–30 per window (can reach 50–60 in a rolling 10 s across a boundary); token bucket (more state); reserved concurrency on submit (limits concurrency, not rate); PENDING_ENTROPY with a delay queue (a second mapped queue adds ≈ 648k SQS requests/month and breaks $0).
- **Why:** any rolling 10 s window overlaps at most two aligned windows, so 15 per window guarantees ≤ 30, and fail-fast satisfies fail-closed.

## D11 Metrics through EMF

- **Chose:** a ten-line Embedded Metric Format emitter, namespace `QMC/<env>`, no dimensions, eight metrics.
- **Alternatives:** `PutMetricData` (needs `Resource: "*"` with a namespace condition and an API call per invocation); Powertools Metrics (a layer or dependency).
- **Why:** EMF needs only the log permissions each function already has, so no IAM wildcard; ids go in properties so the metric count stays at eight.

## D12 Concurrency controls

- **Chose:** event source mapping `maximum_concurrency = 5`; worker reserved concurrency 10, set in James's `dev.tfvars` once the Day 0 quota increase lands. *(review)* The root default is `null`, so a grader's fresh low-quota account can still apply.
- **Alternatives:** reserved concurrency alone (AWS's own demo shows throttled SQS messages landing in the DLQ); no caps (cost runaway).
- **Why:** the mapping stops polling at its cap instead of throttling; reserved concurrency satisfies the assignment once the account allows it.

## D13 Deterministic zips, NumPy vendored

- **Chose:** `scripts/build_lambdas.py` builds both zips outside Terraform: sorted entries, fixed timestamps, no bytecode; NumPy from a hash-pinned manylinux wheel in the worker zip only.
- **Alternatives:** the AWS SDK for pandas layer (AWS chooses the NumPy version, bad for replay); a self-built layer (an extra versioned resource); container images (ECR is not free).
- **Why:** one artifact per function, NumPy version under our control, stable `source_code_hash` (no drift).

## D14 Sharding and memory

- **Chose:** 10 × 100k for a 1M-path job; chunk 131,072; worker 1,024 MB / 10 s; API 512 MB / 10 s; shards 1–50.
- **Alternatives:** more, smaller shards (shows more fan-out, more cold starts and writes); larger memory (faster init, more GB-s).
- **Why:** simulation takes milliseconds per shard, so overhead dominates; 10 shards show fan-out clearly at a fraction of the free GB-s.

## D15 Plain Monte Carlo by default, antithetic as an option

- **Chose:** plain MC; antithetic variates as an opt-in field (stretch). *(Antithetic selected by James as a stretch goal.)*
- **Alternatives:** control variates (≈ 6.9× variance reduction, needs extra cross-moments in the shard contract).
- **Why:** plain MC meets the 3-SE test with a trivially auditable SE; antithetic fits the `{sum, sum_sq, n}` contract when `n` counts pairs.

## D16 SQS parameters

- **Chose:** standard queue, SSE-SQS, batch 1, `ReportBatchItemFailures`, worker timeout 10 s, visibility 60 s, `maxReceiveCount = 3`, caught errors shorten visibility to 5 s, main retention 1 day, DLQ 14 days.
- **Alternatives:** the research default of 30 s / 180 s (DLQ after ≈ 9 minutes); AWS's suggested `maxReceiveCount ≥ 5` (absorbs throttle-induced receives, which `maximum_concurrency` already prevents); FIFO (still at-least-once to the consumer).
- **Why:** planning shortened the timings so a forced failure reaches the DLQ in ≈ 15 s and an uncaught crash in ≈ 3 minutes, which the demo and drills need; visibility stays at 6× the timeout.

## D17 Encryption without customer-managed keys

- **Chose:** SSE-SQS on both queues; DynamoDB's default AWS-owned key; `aws/ssm` for the SecureString; the alerts SNS topic unencrypted.
- **Alternatives:** customer-managed keys everywhere ($1/month each, the whole Budget); SNS with the AWS-managed key (Budgets and CloudWatch cannot publish to it).
- **Why:** encrypted at rest at $0; the Checkov findings are suppressed with these reasons.

## D18 X-Ray off

- **Chose:** no tracing; structured JSON logs with `job_id` and `shard_index`.
- **Alternatives:** X-Ray active tracing (free under 100k traces, but its write actions need `Resource: "*"`).
- **Why:** the no-wildcard IAM rule; logs and metrics cover the debugging need.

## D19 GitLab primary with GitLab CI, mirrored to GitHub

- **Chose:** GitLab (labs.gauntletai.com) is the primary remote with GitLab CI; a push mirror keeps the GitHub repository (the one we submit) current. If no CI runner is available on labs.gauntletai.com, the same jobs run as GitHub Actions on the mirror. No deploys from CI. *(James)*
- **Alternatives:** GitHub primary with Actions (the research default, matches the deliverable directly); GitLab primary with CI on GitHub.
- **Why:** James's workflow and issue tracking live on GitLab (as on the telemetry project); the mirror satisfies the GitHub deliverable.

## D20 Terraform versions, state and layout

- **Chose:** Terraform ≥ 1.10, AWS provider `~> 6.0`, local state at `$HOME/.qmc/state/<env>.tfstate` and *(review)* settings at `$HOME/.qmc/<env>.tfvars`, five local modules, `default_tags`, committed lock file.
- **Alternatives:** S3 remote state (cents per month, not $0, and a bootstrap problem); state inside the repo checkout (each worktree would have its own state).
- **Why:** one operator, graded project; a state path outside the worktrees lets the integrator and WS1 apply the same stack from different checkouts, one at a time.

## D21 Stretch goals

- **Chose:** CloudWatch dashboard, Checkov in CI, per-job cost attribution, antithetic variates; the replay diff report is core. *(James)*
- **Not attempted:** Step Functions (≈ $0.31/month at 100 jobs/day), PENDING_ENTROPY (second mapped queue breaks $0), ephemeral per-branch environments (needs remote state, doubles metrics), portfolio VaR, CI deploys.
- **Why:** each chosen item is low effort and shows judgement; each skipped one costs money or a day. *(review)* The dashboard is cut last, because it is the visible evidence for the assignment's "custom metrics" (EMF metrics are not Terraform resources).

## D22 Timeline

- **Chose:** Day 0 Monday night; build Tuesday 2026-10-13 and Wednesday 2026-10-14; G0 Tue 12:00 PM, G1 Tue 6:00 PM, G2 Wed 12:00 PM, G3 Wed 4:00 PM, G4 Wed 9:00 PM; buffer to midnight. *(James)*
- **Alternatives:** Wednesday–Thursday with a full day for prerequisites.
- **Why:** prerequisites are small enough for Monday night and Tuesday morning.

## D23 Process

- **Chose:** the telemetry project's process: WS0 freezes a contract, six workstreams in parallel git worktrees with path ownership, contract changes through WS0, GitLab milestones as gates, an AI usage log per session. WS1 and WS5 start before G0.
- **Alternatives:** one sequential session.
- **Why:** it worked last time; Terraform is the long pole and depends only on HANDOFF's names, not on `qmc_core`.

## D24 Region us-east-1

- **Chose:** us-east-1. **Why:** Qrypt's only documented endpoint is `api-eus` (East US), so latency on the submit path is lowest there; Budgets are global anyway.

## D25 Paid-plan AWS account

- **Chose:** the deploy account is on the Paid plan; the $0 claim rests on always-free allowances alone. **Why:** on the Free plan, overage silently consumes credits and the account is suspended after six months, which would hide a broken cost design.

## D26 Grader access under AWS_IAM

- **Chose:** Terraform creates an invoke-only IAM user `qmc-<env>-grader` (and a `qmc-<env>-client` role); its access key is created by CLI and sent privately to the program contact; the README also lets graders deploy into their own account.
- **Alternatives:** an access key resource in Terraform (the secret would land in state); switching to a bearer key.
- **Why:** keeps D5 and keeps every secret out of state and the repo.

## D27 Job status model

- **Chose:** `QUEUED` stored at submit; `RUNNING` derived for display; `COMPLETE`, `PARTIAL`; `FAILED` only for an enqueue failure after the job was recorded. A seeding failure writes no job.
- **Why:** one stored transition per job keeps the conditions simple and race-safe.

## D28 Fault injection for the forced-failure demo

- **Chose:** a request field `fault_injection.fail_shards`, honoured only when the deployment sets `enable_fault_injection = true`.
- **Alternatives:** a poison message sent by hand (trips the DLQ alarm but cannot show PARTIAL on a real job).
- **Why:** the demo must show PARTIAL and the alarm on one job; the flag keeps it off by default.

## D29 Job listing via a JOBLIST partition

- **Chose:** submit writes a `JOBLIST` item keyed by creation time; `GET /jobs` queries it newest first. **Alternatives:** Scan (reads the whole table); a GSI (consumes the shared free capacity). **Why:** one extra write unit per job; a fresh deployment naturally returns an empty list.

## D30 Black-Scholes reference in every result

- **Chose:** each job result includes the closed-form price, the z-score and `within_3se`. **Why:** the correctness check is visible to analysts and auditors and in the demo, not only in tests.

## D31 Validation limits

- **Chose:** `paths` 2–10,000,000; `shards` 1–50 and ≤ `paths`; at most 1,000,000 paths per shard; parameter bounds in HANDOFF §5.2; all configurable. **Why:** cost runaway and the worker timeout, with headroom for ten times the reference job.

## D32 Reaper deadline of 900 s

- **Chose:** a job still `QUEUED` 900 s after submission with missing shards is reaped to PARTIAL on read. **Alternatives:** `maxReceiveCount × visibility + slack` = 240 s (could reap legitimate jobs waiting in a burst backlog). **Why:** crashes are rare; the forced-failure path uses last-attempt marking and does not wait for the reaper.

## D33 Apply discipline *(review)*

- **Chose:** state and settings live in `$HOME/.qmc/`, outside every worktree. Until G1, WS1 applies from its branch rebased on `main` and announces it; from G1 on, only the integrator applies, from the `main` checkout. `make apply`/`destroy` refuse a checkout whose `HEAD` does not contain `origin/main` unless `ALLOW_BRANCH_APPLY=1`. Each build writes `build/manifest.json`, and the deployed git sha is the `QMC_BUILD_SHA` environment variable, shown by `GET /health`. Drills that need a configuration change are applied by the integrator with `EXTRA_VARS` and reverted by a normal apply.
- **Alternatives:** any workstream applies from its own worktree (the review's failure case: a drift check from WS4's worktree redeploys stale code with default settings, deleting the grader user and disabling fault injection).
- **Why:** with shared state, whoever applies decides what is deployed; this makes it always `main`, and visible.

## D34 G0 split *(review)*

- **Chose:** G0a at 9:45 AM (skeleton, final Makefile, `qmc_core` interface stubs on `main`), G0 at noon (implementations), the NumPy/SIMD pin by 2:00 PM (QMC-099).
- **Alternatives:** one G0 at noon with everything (WS2–WS4 idle until noon, WS1 without a skeleton on `main`; likely to slip to 2–3 PM).
- **Why:** WS2, WS3 and WS4 can code against signatures; only the worker deploy and the golden test need the final pin, and not before mid-afternoon.

## D35 AI usage logs per workstream *(review)*

- **Chose:** each workstream appends to its own `docs/ai-log/WSn.md`; WS5 folds them into `AI_USAGE.md` at G4.
- **Alternatives:** everyone appends to `AI_USAGE.md` (a merge conflict at every gate); a union merge driver.
- **Why:** no conflicts and a clean final document.

## Review refinements folded into earlier entries and HANDOFF *(review)*

- PARTIAL writes are conditioned on the done-count snapshot they were computed from (`size(done_shards) = n`), and GET reads the job consistently: closes a race where a reaper could mark a just-completed shard failed (I4).
- The worker's SIMD self-check enforces only when `QMC_ENFORCE_PINNED_DISPATCH=true` (Lambda and the golden CI job), so unit tests on AVX-512 CI runners still import the worker.
- EMF and JSON logs are written to stdout, never through `logging` (the Lambda runtime's prefix stops EMF extraction).
- The GitHub mirror token needs workflow permission; GitHub and GitLab `main` shas are compared at every gate.
- The DLQ is warmed at least 15 minutes before a drill or recording (SQS metrics for an inactive queue can take up to 15 minutes); drill timeout 20 minutes.
- All DynamoDB attribute names go through placeholders (reserved words); `mark_failed` returns the old item on refusal so the worker never needs `GetItem`; the API gets `BatchGetItem` for `GET /jobs`; an IAM allow-list test runs over every moto test.
- Aggregation never divides by zero (N = 0 or 1); antithetic needs `paths ≥ 4`.
- The SQS message field is `shard_paths`; the job item stores `algo_version`; queues deny non-TLS access; the worker's runtime is pinned (`update_runtime_on = FunctionUpdate`) and `fma` joins the replay fingerprint.
- Benchmarks pace submissions under our own limiter; cold starts are measured from natural ones; replay ×20 runs in four spaced batches; `make e2e` is rerunnable (`--fresh` for the empty-list check) and observes progress.
- The G4 final sequence (destroy, fresh-clone apply recorded, confirm SNS, warm DLQ, video, grader key last, freeze) is fixed in HANDOFF §2.
