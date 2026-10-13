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

## D10 Qrypt limiter: 2 s minimum gap, no retry on Qrypt 429, fail fast *(contract changes 004, 005, 006; HANDOFF 1.13)*

- **Chose:**
  - A **minimum gap of 2 s between Qrypt calls** (`QRYPT_MIN_GAP_MS` = 2000). One DynamoDB item `RL#qrypt/LAST` is updated with a conditional `SET at_ms = now` that succeeds only if `at_ms <= now - gap` (race-free).
  - At most 1 call per 2 s, so **≤ 5 in any rolling 10 s**, against the assignment's 30.
  - Refused → 429 `ENTROPY_RATE_LIMITED` with `Retry-After` = seconds until the gap ends.
  - **No retry on a Qrypt 429**, because a retry cannot succeed inside the gap. At most one jittered retry on a Qrypt 5xx or network error, within the 2.5 s deadline, and only if the gap allows a new slot. Never retry 400, 401 or 403.
  - If Qrypt 429s persist at 2 s, raise `qrypt_min_gap_ms` in Terraform; no code change is needed.
- **History.** Each step was forced by Qrypt's measured behaviour (HANDOFF R24, `docs/TEST_RESULTS.md`, `docs/BENCHMARKS.md`):
  1. **15 per aligned 10 s window** (the original choice). Any rolling 10 s window overlaps at most two aligned windows, so 15 per window guaranteed ≤ 30, the assignment's figure. Burst at 12:01:50Z (build `d0af0b1`): 40 concurrent submits gave 15 × 202 and 25 × 429. Qrypt answered all 15 with 200; it was not enforcing its 1 call/s rating then (that morning, up to 8 calls in one second all got 200).
  2. **10 per 10 s** (contract change 004), because the trial key is rated 1 call/s. Burst at 12:55:43Z (build `f3873f8`): the limiter let 10 calls through in about one second. Qrypt answered the first with 200 and the other 9 with 429. Result: 1 × 202, 30 × 429 and 9 × 503 `QRYPT_RATE_LIMITED`.
  3. **1 per aligned 1 s window** (contract change 005). Burst at 13:13:37Z (build `6dcb3c2`): 1 × 202, 38 × 429 (`Retry-After: 1`) and 1 × 503. Qrypt refused a call 1.19 s after the previous one, so its limit is sliding, not aligned to the second. James first chose to keep 1 s windows and document this, with a 2 s gap as the fallback "if it remains a problem" (HANDOFF 1.12).
  4. **2 s minimum gap** (contract change 006, the current choice). Measured under 006 (build `e77a626`): a burst at 13:35:56Z gave 1 × 202 and 39 × 429 (`Retry-After` 1 or 2 s), with 0 Qrypt 429s and nothing written for the rejected submits. A steady check at 2.5 s pacing (13:36:48Z) made 5 Qrypt calls 2.49–3.03 s apart, all 200, max 4 in any rolling 10 s. That is no Qrypt 429 in the burst or the steady check; two runs, not a guarantee. 006 was adopted because the 1 s-window trigger fired: `make bench` at a steady 1.5 s pacing (13:20–13:22Z, build `6dcb3c2`) still got 3 Qrypt 429s. Two came 1.519 s and 1.486 s after the previous call, and the 285 ms retry after one of them also got 429.
- Every rejected submit in these runs failed closed: a 503 naming the cause, no job written (I1). No run exceeded 30 calls in any 10 s; the measured maximum was 15.
- **Alternatives:**
  - Keep 1 s windows and document them. Steady use at 1.5 s pacing still fails some submits.
  - A sliding-window counter. More state and expressions, for no benefit over a single last-call timestamp at cap 1.
  - Cap 25–30 per 10 s. This can reach 50–60 in a rolling 10 s across a window boundary.
  - A token bucket (more state).
  - Reserved concurrency on submit (it limits concurrency, not rate).
  - PENDING_ENTROPY with a delay queue. A second mapped queue adds ≈ 648k SQS requests/month and breaks $0.
- **Why:** a single last-call timestamp is one conditional write per submit. It matches Qrypt's real sliding limit, so excess submits get our own 429 with an exact `Retry-After` instead of a Qrypt failure. Failing fast satisfies fail-closed (I1).

## D11 Metrics through EMF

- **Chose:** a ten-line Embedded Metric Format emitter, namespace `QMC/<env>`, no dimensions, eight metrics.
- **Alternatives:** `PutMetricData` (needs `Resource: "*"` with a namespace condition and an API call per invocation); Powertools Metrics (a layer or dependency).
- **Why:** EMF needs only the log permissions each function already has, so no IAM wildcard; ids go in properties so the metric count stays at eight.

## D12 Concurrency controls

- **Chose:** event source mapping `maximum_concurrency = 5`; worker reserved concurrency 10, set in James's `dev.tfvars` once the account's concurrency limit is ≥ 110 (D40, contract change 002; the project account's limit is 400, so dev sets 10). *(review)* The root default is `null`, so a grader's fresh low-quota account can still apply.
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

## D19 GitLab primary with GitLab CI; GitHub as a second remote pushed at gates *(changed 2026-10-13, HANDOFF 1.8)*

- **Chose:**
  - GitLab (labs.gauntletai.com) is the primary remote. GitLab CI runs on James's local Mac runner (docker executor, untagged jobs), so the `golden` job there is emulated amd64.
  - **GitHub is a plain second remote, not a mirror.** The integrator pushes `main` and tags to it at every gate. It is the submitted repository, and its GitHub Actions workflow runs natively on x86 on each push.
  - No deploys from CI. *(James)*
- **Alternatives:**
  - GitHub primary with Actions (the research default; matches the deliverable directly).
  - A GitLab push mirror to GitHub (the original choice). It needs a GitHub token with workflow scope and can stop silently (old R20).
  - GitLab CI on shared runners (none available on labs.gauntletai.com, old R18).
- **Why:**
  - James's workflow and issue tracking live on GitLab, as on the telemetry project.
  - Manual pushes at the gates mean no mirror token to manage. A missed push is caught by the GitHub-vs-GitLab `main` sha check that the integrator runs at every gate and before submission (R20).
  - The local runner resolves R18, and GitHub Actions adds a native-x86 CI run, which the emulated `golden` job cannot provide (D36).

## D20 Terraform versions, state and layout

- **Chose:** Terraform ≥ 1.10, AWS provider `~> 6.0`, local state at `$HOME/.qmc/state/<env>.tfstate` and *(review)* settings at `$HOME/.qmc/<env>.tfvars`, five local modules, `default_tags`, committed lock file.
- **Alternatives:** S3 remote state (cents per month, not $0, and a bootstrap problem); state inside the repo checkout (each worktree would have its own state).
- **Why:** one operator, graded project; a state path outside the worktrees lets the integrator and WS1 apply the same stack from different checkouts, one at a time.

## D21 Stretch goals *(amended 2026-10-13 by James, HANDOFF 1.17)*

- **Done:** CloudWatch dashboard (QMC-118); replay diff report (core).
- **Building:**
  - Checkov as a blocking CI job (QMC-119).
  - Per-job cost attribution (QMC-126).
  - Antithetic variates (QMC-108).
  - An **optional Step Functions watcher, off by default** (QMC-127, contract change 007).
  - **Opt-in PENDING_ENTROPY** via an async self-invoke with backoff (QMC-128, contract change 008).
  - *TBD: each entry is updated as it lands, with its evidence.*
- **Parked, with reasons:** portfolio VaR (D45) and ephemeral per-branch environments (D46). James may revisit them later.
- **Out:** CI deploys.
- **Original choice (2026-10-12):** dashboard, Checkov in CI, cost attribution and antithetic variates. Not attempted then:
  - Step Functions, ≈ $0.31/month at 100 jobs/day if always on.
  - PENDING_ENTROPY as a second mapped queue, which breaks $0.
  - Ephemeral environments.
  - Portfolio VaR.
  - CI deploys.
- **Why the amendment:** Step Functions and PENDING_ENTROPY were brought back in forms that keep $0 by default: the watcher is off unless enabled, and PENDING_ENTROPY uses a self-invoke rather than a second mapped queue (007, 008). The dashboard is still the visible evidence for the assignment's "custom metrics" (EMF metrics are not Terraform resources).

## D22 Timeline

- **Chose:** Day 0 Monday night; build Tuesday 2026-10-13 and Wednesday 2026-10-14; G0 Tue 12:00 PM, G1 Tue 6:00 PM, G2 Wed 12:00 PM, G3 Wed 4:00 PM, G4 Wed 9:00 PM; buffer to midnight. *(James)*
- **Alternatives:** Wednesday–Thursday with a full day for prerequisites.
- **Why:** prerequisites are small enough for Monday night and Tuesday morning.

## D23 Process

- **Chose:** the telemetry project's process: WS0 freezes a contract, six workstreams in parallel git worktrees with path ownership, contract changes through WS0, GitLab milestones as gates, an AI usage log per session. WS1 and WS5 start before G0.
- **Alternatives:** one sequential session.
- **Why:** it worked last time; Terraform is the long pole and depends only on HANDOFF's names, not on `qmc_core`.

## D24 Region us-east-2 *(contract change 001)*

- **Chose:** us-east-2 (Ohio).
- **Alternatives:** us-east-1, the original choice for its closeness to Qrypt's only documented endpoint, `api-eus` (East US); a second, standard-sign-up account in us-east-1; turning on advanced features to lift the Region pin.
- **Why:** the account is a new-AWS-experience project pinned to us-east-2 by managed SCPs and RCPs, so Regional resources cannot be created elsewhere. Ohio is still near `api-eus`. Nothing was deployed yet, so the move cost only text and defaults. Budgets are global anyway.

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

## D36 NumPy 2.5.4 pin, and the self-check as the real SIMD guard *(G0, QMC-099)*

- **Chose:** NumPy **2.5.4**, pinned by wheel sha256 in `requirements/worker.txt`, with `NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR"`. Under that setting, in `public.ecr.aws/lambda/python:3.13`, float64 `exp` dispatches to **`X86_V3`** (AVX2 + FMA). The other ufuncs in the simulation (`add`, `multiply`, `subtract`) have no AVX-512 kernels, so every host with AVX2 runs the same code (HANDOFF §12).
- **Finding:** NumPy 2.5.4 **silently ignores unknown names** in `NPY_DISABLE_CPU_FEATURES`: `FOO`, `AVX512F` and `AVX2` all leave the target unchanged. A typo would therefore disable nothing and give no warning.
- **Consequences:**
  - `tests/core/test_dispatch_lambda_image.py` asserts that every listed name is a real dispatch group.
  - The fail-closed self-check at worker init (D2) is the guard that actually counts. If `exp` ever dispatches to an AVX-512 target, the worker refuses to run.
  - Setting `NPY_DISABLE_CPU_FEATURES=X86_V3` drops `exp` to the `X86_V2` baseline, which shows the variable does take effect.
- **Alternatives:** relying on the environment variable alone, which a typo would silently defeat; pinning the baseline with `X86_V3` disabled as well, which is slower and unnecessary while AVX2 is universal on Lambda x86.
- **Evidence on Lambda (G1, 2026-10-13, build `c768aea`):** job `5358ec35af8a450d9eba939da9f610d9` recorded the worker fingerprint `numpy 2.5.4, glibc 2.34, exp_target X86_V3, dispatch_enforced true, avx512f false` (`docs/ai-log/WS2.md`). That host had no AVX-512, so this shows the pinned target with enforcement on, not the self-check refusing an AVX-512 host. No AVX-512 Lambda host has been seen yet; the refusal path is proven by unit tests (`tests/core/test_sim.py::test_self_check_raises_when_enforced`, `tests/worker/test_happy_path.py::test_init_self_check_raises_before_any_message`).
- **Evidence in emulation:** the target was measured locally under Docker amd64 emulation on Apple silicon (no AVX-512 visible). CI's `golden` job passed (7 tests) in [pipeline 52736](https://labs.gauntletai.com/jamesmcgirr/quantum-seeded-monte-carlo/-/pipelines/52736) on `ea3820c`, but on James's Mac runner (`via Jamess-MacBook-Pro.local` in the job log), so it is also emulated amd64. - **Native x86:** the `golden` job passed in [GitHub Actions run 38392842904](https://github.com/jrmcgirr/quantum-seeded-monte-carlo/actions/runs/38392842904) on `main` `d0af0b1`. It runs in the `public.ecr.aws/lambda/python:3.13` container on an `ubuntu-24.04` (x86_64) runner, with `QMC_ENFORCE_PINNED_DISPATCH=true` (`.github/workflows/ci.yml`). All 7 jobs were green.
- **Bit-for-bit evidence on real Lambda hardware:** WS4's `make e2e` (build `c768aea`; job `edaff921…`, replay `d6afc058…`) replayed bit-identically: 10 shards, no diffs, `fingerprint_match: true`. The shards ran on two reported CPU models (`Intel(R) Xeon(R) Processor @ 2.50GHz` and `Intel(R) Xeon(R) Processor`), all with `exp_target X86_V3` and `dispatch_enforced true` (`docs/TEST_RESULTS.md`, QMC-142).

## D37 Grader invoke policy inline on the user *(first apply, HANDOFF 1.5)*

- **Chose:** the grader user `qmc-<env>-grader` gets its invoke policy as an **inline user policy**, not through an IAM group. Checkov `CKV_AWS_40` ("IAM policies attached only to groups or roles") is skipped with this reason.
- **Why:** the new-AWS-experience Paid-plan SCP `DenyIAMRestrictedActions` denies `iam:CreateGroup`, `iam:PutGroupPolicy` and `iam:AddUserToGroup`, as found at WS1's first apply. `iam:CreateUser`, `iam:PutUserPolicy` and `iam:CreateAccessKey` are still allowed, so D26 is unchanged.
- **Alternatives:** a group (denied by the SCP); a managed policy attached to the user (Checkov flags it the same way, and it adds a resource); dropping the user and having graders assume the `qmc-<env>-client` role (that needs a principal in the grader's own account, which we cannot rely on).

## D38 Worker runtime pinned with `aws_lambda_runtime_management_config` *(first apply, HANDOFF 1.5)*

- **Chose:** a separate `aws_lambda_runtime_management_config` resource sets `update_runtime_on = "FunctionUpdate"` on the worker.
- **Why:** AWS provider 6.68 has no `runtime_management_config` block on `aws_lambda_function`, so HANDOFF §8.2's original form does not exist. The pin is still needed so AWS cannot change glibc (and so `exp`) under a deployed worker during grading (§7.9, I5).
- **Alternatives:** leaving runtime updates on `Auto` (a mid-grading runtime update could break replay); setting it by CLI (drift, banned).

## D39 No `kms:Decrypt` grant for the SecureString *(G1, HANDOFF 1.6 §8.3)*

- **Chose:** the API role has `ssm:GetParameter` on the token parameter and **no** `kms:Decrypt` statement.
- **Why:** the token parameter is encrypted with the AWS-managed `alias/aws/ssm` key, whose key policy authorizes account principals calling through SSM. WS2's first live submit (job `5358ec35af8a450d9eba939da9f610d9`, Qrypt HTTP 200, 2026-10-13) read it with `GetParameter(WithDecryption=True)` using only `ssm:GetParameter` (`docs/ai-log/WS2.md`).
- **Alternatives:** the conditional grant HANDOFF §8.3 held in reserve (`kms:Decrypt` on the `aws/ssm` key with `kms:EncryptionContext:PARAMETER_ARN`). It was not needed, so leaving it out keeps the role smaller.

## D40 Reserved-concurrency threshold 110, not 1,000 *(contract change 002, HANDOFF 1.7)*

- **Chose:** set `worker_reserved_concurrency = 10` whenever the account's Lambda concurrency limit is at least **110**. AWS keeps 100 unreserved, the worker reserves 10, and the API reserves none. The project account's limit is 400 (`aws lambda get-account-settings` → `ConcurrentExecutions: 400`; Service Quotas `L-B99A9384`), so dev uses 10 from the first apply. The Terraform default stays `null`, so a grader's fresh account can still apply (R22). `maximum_concurrency = 5` is unchanged.
- **Alternatives:**
  - Request 1,000 anyway. It is free and usually approved, but G1 would wait on an AWS ticket, and the extra headroom buys nothing while `maximum_concurrency` caps fan-out at 5.
  - Leave the reservation `null` on dev. Fan-out would still be capped, but the worker would lose the hard upper bound that HANDOFF §10 lists against cost runaway.
- **Why:** the old 1,000 threshold blocked a setting that already works. With this change, the 10-unit hard cost bound (I7) applies from the first apply instead of after a quota ticket.

## D41 Qrypt 403 is `QRYPT_FORBIDDEN` *(contract change 003, HANDOFF 1.9)*

- **Chose:**
  - Rename the API error `QRYPT_QUOTA_EXHAUSTED` to **`QRYPT_FORBIDDEN`** (still 503). Its message names both possible causes: "Qrypt refused the request (403): the token is invalid or expired, or the entropy quota is used up."
  - 401 stays `QRYPT_AUTH_FAILED`; in practice that means a missing or malformed header.
  - The live fail-closed drill therefore shows 503 `QRYPT_FORBIDDEN`.
- **Why:** WS4's fail-closed drill with a decoy token (build `d0af0b1`) got HTTP 403, not 401, and the API reported a quota problem for what was a bad token. WS0 then probed Qrypt once with a fake token: `Bearer NOT-A-REAL-TOKEN` → 403 `{"error":"Forbidden or entropy quota reached"}`, and no `Authorization` header → 401. Qrypt sends the same status and body for a rejected token and an exhausted quota, so no client can tell them apart.
- **Alternatives:**
  - Keep the code name and change only the message. The code that graders see, and the `cause` property on `SeedingFailures`, would still be misleading.
  - Map 403 to `QRYPT_AUTH_FAILED`. A real quota exhaustion would then be reported as a bad token.
  - Parse the 403 body. It is the same for both causes.
- **Invariants:** none change. I1 (fail closed, nothing written, cause named) holds, and the named cause is now accurate.

## D42 Protected `main`, merge requests only *(James, 2026-10-13; HANDOFF 1.15)*

- **Chose:**
  - No direct pushes to `main` for anyone, WS0 included: push access is no one, merge is maintainers only, and force push is off.
  - Every change lands through a merge request whose pipeline must pass.
  - Source branches are kept after merge.
  - GitLab CI runs only for merge-request pipelines (and manual web runs), not for plain pushes. GitHub Actions runs on push to GitHub `main` (D19).
  - Contract changes that span several workstreams are batched by stacking their branches into one merge request, as was done for 003.
- **Alternatives:**
  - A `staging` integration branch, with workstream merge requests into `staging` and one promotion merge request into `main`. Tried and dropped the same day (HANDOFF 1.14 → 1.15): protecting `main` already gives the guarantee wanted, without an extra branch to keep in sync.
  - Unprotected `main` with direct integrator pushes, the original flow (D23).
- **Why:** protection makes "every change passed CI" a rule that GitLab enforces, rather than a convention.

## D43 Budget alerts through SNS only *(James, 2026-10-13)*

- **Chose:** the `qmc-monthly` Budget's notifications (actual > 1%, actual > 100%, forecast > 100%) publish **only** to the SNS topic `qmc-dev-alerts`, which emails James through its confirmed subscription. The direct email subscriber on the Budget is removed (WS1).
- **Why:** in the new AWS experience, a Budget's direct email recipient goes through AWS User Notifications and must be verified there. In this account nobody can: the new-AWS-experience project SCP has a statement `DenyNotificationContactsOperations` that denies `notifications-contacts:*` ([SCPs and RCPs for projects](https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html)). The SNS path already delivers the same alerts. This is also why the topic is unencrypted (D17): Budgets cannot publish to a topic that uses the AWS-managed SNS key.
- **Alternatives:**
  - Keep the direct email subscriber. It cannot be verified, so its emails would not arrive.
  - Activate advanced features, which removes that SCP. That changes the account's managed guardrails, which is out of scope.
- **Applied:** WS1's MR !24 was applied from `main` `8a289d8` at 13:43:24Z (3 changed; the plan afterwards showed no changes).
- **Verified:** read-only Budgets calls (`DescribeNotificationsForBudget`, `DescribeSubscribersForNotification`) show that each of the three notifications on `qmc-monthly` has exactly one subscriber, of type SNS, on `qmc-dev-alerts`.

## D44 Grader user without `force_destroy`; its key is deleted by CLI before destroy *(drift check, 2026-10-13; HANDOFF 1.16, R25)*

- **Chose:**
  - `force_destroy = false` on `aws_iam_user` `qmc-<env>-grader` (MR !28).
  - The grader's CLI-created access key is deleted by CLI before any `make destroy`, and the G4 sequence does so.
  - `destroy` reads `force_destroy` from state, so changing it needs an apply first.
- **Why:**
  - With `force_destroy = true`, the AWS provider calls `iam:DeleteLoginProfile` while destroying the user. The new-AWS-experience SCP denies `iam:*LoginProfile*` ([SCPs and RCPs for projects](https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html), statement `DenyIAMRestrictedActions`).
  - So the drift check's destroy (15:19Z) removed everything else and then stopped on the user.
  - With `force_destroy = false` that call is not made. `DeleteUser` then succeeds as long as the user has no access key; otherwise it fails with `DeleteConflict`.
- **Evidence:** after the fix the drift check passed: no orphans, apply from empty in 65 s (33 resources), `e2e --fresh` 12/12, then a plan with no changes (`docs/TEST_RESULTS.md`, 15:18–15:34Z). It was finished by hand after the first destroy failed, not as one uninterrupted run (TEST_RESULTS says so).
- **Alternatives:**
  - Keep `force_destroy = true` (destroy always fails here).
  - Manage the access key in Terraform. Its secret would then enter state (D26).

## D45 Portfolio VaR: parked *(James, 2026-10-13; QMC-129, #55)*

- **Decision:** not built. James may revisit it later.
- **What it would take** (2026-10-13 research relayed by WS0, issue #55):
  - **A different fan-in.** Our shards return `{sum, sum_sq, n}`, which add up exactly in any order because `math.fsum` makes the result independent of order. A VaR is a **quantile**, and quantiles do not add. Reproducible fan-in would need a **fixed-edge histogram per shard**: counts plus a per-bin `fsum`. The VaR is then known to within one bin width, and expected shortfall only to within bounds (Acerbi–Tasche).
  - **Correlated assets.** These need a Cholesky factor of the correlation matrix, computed in pure Python with `math.fsum`. `@`, `np.dot` and `np.einsum` are out, because BLAS picks SIMD kernels by CPU, which would break bit-identical replay (D2, I5; PRIMER, "Pricing the option is trivial…").
  - **Effort:** ≈ 43 h. It reopens every interface (request schema, SQS message, shard item, aggregation, replay compare) before G4. A minimal version (histogram, ≤ 10 assets, ≤ 2M paths, a single-asset closed-form check) is ≈ 1.5 days.
- **Why parked:** it cannot land inside the timebox without reopening every frozen interface before G4.

## D46 Ephemeral per-branch environments: parked *(James, 2026-10-13; QMC-129, #55)*

- **Decision:** not built. James may revisit it later.
- **Why** (2026-10-13 research relayed by WS0, issue #55):
  - **No OIDC.** CI needs AWS credentials. The project SCP's `DenyIAMRestrictedActions` denies `iam:*Provider*` ([SCPs and RCPs for projects](https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html)), so no GitHub or GitLab OIDC identity provider can be created. The only option would be a **long-lived, effectively admin-equivalent access key stored in the CI of a public repository**.
  - **Free-tier limits.** Each environment would bring its own alarms and EMF metrics. The free tier counts ≤ 10 of each per account, across all environments (I7, HANDOFF §10), and dev already uses 8 metrics and 7 alarms.
  - **State.** Per-branch state would need GitLab-managed Terraform state or HCP; today it is local, outside every worktree (D20, D33).
- **Fallback, if ever wanted:** a manually triggered GitLab review environment with a 1 h auto-stop, no alarms or EMF metrics, and a 2 RCU / 2 WCU table, still inside the shared 25/25 DynamoDB allowance.

## Review refinements folded into earlier entries and HANDOFF *(review)*

- PARTIAL writes are conditioned on the done-count snapshot they were computed from (`size(done_shards) = n`), and GET reads the job consistently: closes a race where a reaper could mark a just-completed shard failed (I4).
- The worker's SIMD self-check enforces only when `QMC_ENFORCE_PINNED_DISPATCH=true` (Lambda and the golden CI job), so unit tests on AVX-512 CI runners still import the worker.
- EMF and JSON logs are written to stdout, never through `logging` (the Lambda runtime's prefix stops EMF extraction).
- The GitHub mirror token needs workflow permission; GitHub and GitLab `main` shas are compared at every gate. *(Superseded by D19's 2026-10-13 change: no mirror; the sha comparison stays.)*
- The DLQ is warmed at least 15 minutes before a drill or recording (SQS metrics for an inactive queue can take up to 15 minutes); drill timeout 20 minutes.
- All DynamoDB attribute names go through placeholders (reserved words); `mark_failed` returns the old item on refusal so the worker never needs `GetItem`; the API gets `BatchGetItem` for `GET /jobs`; an IAM allow-list test runs over every moto test.
- Aggregation never divides by zero (N = 0 or 1); antithetic needs `paths ≥ 4`.
- The SQS message field is `shard_paths`; the job item stores `algo_version`; queues deny non-TLS access; the worker's runtime is pinned (`update_runtime_on = FunctionUpdate`) and `fma` joins the replay fingerprint.
- Benchmarks pace submissions under our own limiter; cold starts are measured from natural ones; replay ×20 runs in four spaced batches; `make e2e` is rerunnable (`--fresh` for the empty-list check) and observes progress.
- The G4 final sequence (destroy, fresh-clone apply recorded, confirm SNS, warm DLQ, video, grader key last, freeze) is fixed in HANDOFF §2.
