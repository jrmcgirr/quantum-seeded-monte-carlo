# Quantum-Seeded Monte Carlo — Handoff

Version 1.14, 2026-10-13 (D42 `staging` branch: all workstream MRs target `staging`; CI only on merge requests). 1.13, 2026-10-13 (contract change 006: Qrypt minimum gap 2 s (`QRYPT_MIN_GAP_MS`), no retry on Qrypt 429). 1.12, 2026-10-13 (burst result under 005 accepted as known behaviour: a call just after a window boundary can still get Qrypt 429, fail-closed; 2 s minimum gap is the fallback). 1.11, 2026-10-13 (contract change 005: Qrypt limiter 1 s window, cap 1; `QRYPT_WINDOW_S`). 1.10, 2026-10-13 (contract change 004: Qrypt limiter cap 10 per 10 s). 1.9, 2026-10-13 (contract change 003: Qrypt 403 → `QRYPT_FORBIDDEN`; fail-closed drill expects it). 1.8, 2026-10-13 (D19: GitHub is a plain remote pushed at gates, not a mirror; GitLab CI on James's runner). 1.7, 2026-10-13 (contract change 002: reserved-concurrency threshold 110). 1.6, 2026-10-13 (G1 live: `kms:Decrypt` not needed). 1.5, 2026-10-13 (first apply: runtime management resource, grader policy inline on the user (SCP), `botocore[crt]`). 1.4, 2026-10-13 (G0 contract frozen: §12 pins and the NumPy 2.5 SIMD finding, §4.1 additions). 1.3, 2026-10-13 (G0a interfaces: §4.1 seams, `qrypt_call` log event, shared test fixtures). 1.2, 2026-10-13 (contract change 001: Region us-east-2). 1.1, 2026-10-12 (after an independent review: apply discipline from `main`, G0 split, enforcement flag for the SIMD self-check, snapshot-conditioned PARTIAL writes, stdout EMF, mirror token scope, DLQ metric lag, reserved words, empty-sample guards). Owner: James McGirr. This file is the single source of truth for every coding agent on the project. Read it fully before writing code.

Where this file disagrees with `docs/ASSIGNMENT.md`, **this file wins**; section 3 records how each conflict was resolved. The product view (who, why, success metrics) is in `docs/PRD.md`; the plan, gates and tickets are in `docs/ROADMAP.md`; the research behind every decision is in `docs/PRIMER.md`; the reasoning for each decision is in `docs/DECISIONS.md`.

---

## 1. What we are building

Quant analysts price options with Monte Carlo. Two things go wrong in practice: seeds are weak or reused, so "independent" runs are correlated; and infrastructure is hand-built, so nobody can reproduce a run or explain its bill. Model-risk and audit functions need any reported number to be reproducible.

We build a serverless platform on AWS, fully managed by Terraform, that prices a **European call under Black-Scholes** by Monte Carlo:

1. `POST /jobs` (Lambda Function URL, `AWS_IAM` auth) validates `{S0, K, r, sigma, T, paths, shards}`, fetches **one 1 KiB block of Qrypt quantum entropy**, derives a 256-bit master seed, records the job in DynamoDB and enqueues one SQS message per shard.
2. Worker Lambdas consume SQS, derive each shard's seed from `(master seed, shard index)`, simulate their paths and write `{sum, sum_sq, n}` per shard to DynamoDB. The worker that completes the last shard aggregates the job into an estimate, standard error and 95% confidence interval.
3. `GET /jobs/{id}` shows progress, then `COMPLETE` with the result and its distance from the closed-form Black-Scholes price.
4. `POST /jobs/{id}/replay` re-runs the job from its stored master seed and reports whether the result is **bit-identical**.
5. Everything stays inside AWS **always-free** allowances, with a $1 Budget, custom metrics and alarms as guardrails.

Seven invariants hold no matter what. Each has exactly one enforcement mechanism, named here; never replace it with a check-then-act in application code.

| # | Invariant | Enforced by |
| --- | --- | --- |
| I1 | **Seeded only from Qrypt.** Every job's master seed derives from a Qrypt block fetched for that job (a replay reuses its original's seed). Any Qrypt or secret failure rejects the job with an error naming the cause; nothing is written. No block is ever used twice | Submit order: limiter → Qrypt → entropy-uniqueness put → job put → enqueue (§7.5). `qmc_core.seeds` has no import of `os`, `random`, `secrets` or `time` (test) |
| I2 | **Independent, reproducible shard seeds.** Shard *i*'s stream depends only on `(master seed, i)` | `SeedSequence(master_int, spawn_key=(i,))` → `PCG64DXSM` (§7.2) |
| I3 | **Each shard is counted at most once,** whatever the SQS redelivery | Conditional put on `(JOB#id, SHARD#iiii)` + `ADD done_shards :{i}` to a number set (§7.6) |
| I4 | **COMPLETE ⇔ all *n* shards present and none failed.** Otherwise the job ends `PARTIAL` with the failed indexes; never COMPLETE with a wrong total, never both | Finalize writes conditioned on `status = QUEUED` (and, for COMPLETE, `attribute_not_exists(failed_shards)`); worker last-attempt marking; deadline reaper (§7.7) |
| I5 | **Bit-identical replay** under the same runtime fingerprint; replay *reports* a byte-level comparison rather than assuming one | Pinned NumPy, x86_64, `NPY_DISABLE_CPU_FEATURES` + fail-closed SIMD self-check, fixed chunk size, `math.fsum`, hex-float storage, fingerprint per shard (§7.3, §7.4, §7.9) |
| I6 | **The Qrypt token is never in Terraform state, the repo or the logs** | Parameter created out-of-band; Terraform knows only its name; fetched at runtime (§8.4); logger redaction (§9.3) |
| I7 | **$0 at rest and at 100 jobs/day of 1M paths** | Exactly one SQS event source mapping; provisioned DynamoDB 10/10; ≤ 10 custom metrics and ≤ 10 alarms; no customer-managed KMS keys; 14-day log retention; no Step Functions, NAT, ALB, EC2, RDS, API Gateway, Secrets Manager, S3 or ECR (§8, §10) |

## 2. Deadline, Day 0 and gates

All times US Central (America/Chicago). **Build Tue 2026-10-13 and Wed 2026-10-14; due Wed 2026-10-14.** The finish line is **Wed 9:00 PM**; 9:00–11:59 PM is buffer.

### Day 0 (prerequisites, James, not counted in the timebox; finish before Tue 9:00 AM)

AWS account verification can take hours, and the Qrypt token may need a support email, so start tonight.

| # | Step | Check |
| --- | --- | --- |
| 0.1 | AWS account on the **Paid plan** (D25), root MFA on, billing alerts enabled in Billing preferences, an admin identity for the CLI (IAM Identity Center or an IAM user) | `aws sts get-caller-identity` works with `AWS_REGION=us-east-2` |
| 0.2 | Lambda concurrency quota | `aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions`. If under 110, request 1,000 in Service Quotas now (a reservation must leave at least 100 unreserved, D12; contract change 002) |
| 0.3 | Qrypt: register at portal.qrypt.com, create an **Entropy-only** token, note its expiry, keep an offline copy (it is shown once) | `curl` the endpoint with `size=1` (HANDOFF §7.5 has the call); record 10 latencies from the laptop; read the quota on the portal's usage page. If self-registration is closed, email Qrypt support immediately; the build proceeds against the fake entropy source |
| 0.4 | Store the token out-of-band, keeping it out of shell history and `ps`: `read -rs QRYPT_API_KEY; printf %s "$QRYPT_API_KEY" \| aws ssm put-parameter --name /qmc/qrypt-api-key --type SecureString --value file:///dev/stdin; unset QRYPT_API_KEY`. Note the token's expiry date: it must cover the grading window | `aws ssm get-parameter --name /qmc/qrypt-api-key --query Parameter.Type` → `SecureString` |
| 0.5 | Tools: Terraform ≥ 1.10, AWS CLI v2, tflint, checkov (pipx), uv, Python 3.13, Docker (Lambda-image tests) | `terraform version`, `tflint --version`, `uv --version`, `docker info` |
| 0.6 | Repos: GitLab project exists; create an empty **public** GitHub repo `quantum-seeded-monte-carlo` (no mirror; the integrator pushes to it, D19); GitLab CI runner registered (done: James's Mac, docker executor, run untagged) | `git ls-remote github` works |
| 0.7 | Deployment settings live **outside every worktree** at `$HOME/.qmc/dev.tfvars` (`chmod 600`): `alert_email`, `create_grader_user = true`, `enable_fault_injection = true`, and `worker_reserved_concurrency = 10` once the limit is ≥ 110 (100 unreserved + 10). The Makefile passes `-var-file` (D33) | `make plan` reads it from any checkout |
| 0.8 | Review this package, then `./scripts/bootstrap.sh --apply` (commit, branches, worktrees, GitLab labels/milestones/issues) | `git worktree list` shows six checkouts |

### Gates

| Gate | GitLab milestone | When (Central) | Must be true |
| --- | --- | --- | --- |
| G0a Interfaces on `main` | M0 | Tue 10-13, 9:45 AM | Skeleton, the **final** Makefile (every recipe calls its fixed script path), stub handlers, a provisional NumPy pin, and `qmc_core` **interface stubs** (every public function and dataclass named in CLAUDE.md, with signatures and docstrings, raising `NotImplementedError`) merged to `main`; tag `g0a-interfaces` |
| G0 Contract frozen | M0 | Tue 10-13, 12:00 PM | `qmc_core` implemented with its unit tests green and coverage ≥ 90%; CI pipeline green; tag `g0-contract-frozen` |
| G1 Deployed end to end | M1 | Tue 10-13, 6:00 PM | NumPy version and SIMD feature names pinned in the Lambda image and recorded in §12 (QMC-099, by 2:00 PM); `make apply` from empty succeeds in us-east-2; `make e2e` submits a 1M-path job, observes progress, reaches COMPLETE within 3 SE of Black-Scholes, then replays it **bit-identically**; closed-form, seed and golden-hash tests committed and green |
| G2 Hardened | M2 | Wed 10-14, 12:00 PM | Every edge case in the assignment demonstrated against the deployed stack (§11): fail-closed seeding, duplicate delivery, PARTIAL + DLQ alarm email, rate-limit burst, validation 400s, 404 / empty list; Budget and alarms live, SNS subscription **Confirmed**; apply → plan (no changes) → destroy → apply passes with no orphans |
| G3 Proven | M3 | Wed 10-14, 4:00 PM | Benchmarks recorded (50-submit p50/p95, five 1M-path wall-clock runs, apply-from-empty time); replay ×20 across Lambda hosts all bit-identical; coverage ≥ 80% in CI; Checkov green with justified suppressions; `docs/TEST_RESULTS.md` complete; stretch work merged or cut |
| G4 Finish line | M4 | Wed 10-14, 9:00 PM | README with grader quick-start, architecture diagram, cost note with Budget screenshot, AI_USAGE.md, one-line description, video ≤ 5 min; fresh-clone rehearsal passed in the order of §2 "Final sequence"; stack deployed, SNS subscription Confirmed, endpoint URL in the README; GitHub mirror current |

**At every gate** the integrator pushes `main` and tags to GitHub (`git push github main --tags`), checks that GitHub's `main` sha equals GitLab's, and that both CI systems are green.

**Who starts when.** WS0 starts Tue 9:00 AM and merges G0a to `main` by 9:45 AM. WS1 (Terraform) and WS5 (docs skeletons) start at 9:00 AM, because everything they need (names, settings, IAM, layout) is in this file, and rebase on `g0a-interfaces`. WS2, WS3 and WS4 start at G0a, writing code and tests against the interface stubs; their tests go green once G0 lands.

**Cut line**, in this order, if a gate slips: per-job cost attribution (QMC-126) → antithetic variates (QMC-108) → Checkov suppressions polish (keep the CI job, mark it `allow_failure`) → replay ×20 drops to ×5 → CloudWatch dashboard (QMC-118; last, because it is the visible evidence for the custom metrics). Nothing marked *never cut* in `docs/ROADMAP.md` is cut without asking James.

**Final sequence (G4), in this order:** (1) `make destroy` from the `main` checkout; (2) fresh clone from GitHub into a new folder, follow only the README, `make build && make apply` from empty, recorded on screen (this is the video's apply segment) and timed; (3) confirm the SNS subscription email at once; (4) `make warm-dlq`, then `make e2e` and the remaining video segments (the forced failure at least 15 minutes after the DLQ was warmed, R23); (5) create the grader user's access key by CLI **last** and send it privately; (6) freeze: no destroy or apply until grading ends.

## 3. Decisions (final)

The reasoning, alternatives and sources are in `docs/DECISIONS.md` and `docs/PRIMER.md`.

| # | Decision |
| --- | --- |
| D1 | **Python 3.13** (Lambda `python3.13`, Amazon Linux 2023) with **NumPy 2.x pinned to one exact version and hash** (WS0 records it at G0, §12). |
| D2 | **x86_64** with `NPY_DISABLE_CPU_FEATURES` set on the worker, a **fail-closed SIMD self-check** at init, and a runtime fingerprint stored on every shard. |
| D3 | Master seed = **HKDF-SHA256** over the full 1 KiB Qrypt block, fixed public salt, versioned `info` bound to the job id, 32 bytes out. |
| D4 | Shard seed = `SeedSequence(master_int, spawn_key=(i,))` (default `pool_size=4`) feeding **`PCG64DXSM`**. Never `spawn()` on a reused object; never `master + i`. |
| D5 | Function URL auth **`AWS_IAM`**; callers need both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` in their identity policy. A SigV4 CLI client ships in `clients/qmc.py`. |
| D6 | **One API router Lambda** (`POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/replay`, `GET /health`) and **one worker Lambda**. |
| D7 | **One DynamoDB table**, `PROVISIONED` 10 RCU / 10 WCU, no autoscaling, no GSI, TTL on `ttl`, PITR off, AWS-owned encryption key (no `server_side_encryption` block). |
| D8 | Fan-in: **conditional shard put + number-set `ADD done_shards` + conditional finalize**; lazy finalize on `GET` as a repair path. |
| D9 | PARTIAL: **worker last-attempt marking** (`ApproximateReceiveCount ≥ maxReceiveCount`) **+ deadline reaper evaluated on `GET`**. The DLQ is never consumed; it is alarmed. PARTIAL is final. |
| D10 | Qrypt rate limit: **minimum gap between Qrypt calls, `QRYPT_MIN_GAP_MS` = 2000** (one DynamoDB item `RL#qrypt/LAST`, conditional `SET at_ms = now` if `at_ms <= now - gap`): at most 1 call per 2 s and ≤ 5 in any rolling 10 s (contract change 006; history 15/10 s → 10/10 s → 1/1 s → 2 s gap, each step forced by Qrypt's measured behaviour, R24). Refused → 429 `ENTROPY_RATE_LIMITED` with `Retry-After` = seconds until the gap ends. **No retry on a Qrypt 429** (it cannot succeed inside the gap); at most one jittered retry on Qrypt 5xx or a network error, inside the 2.5 s deadline, only if the gap allows a new slot. No PENDING_ENTROPY queue. |
| D11 | Metrics via **hand-rolled EMF** log lines, namespace `QMC/<env>`, **no dimensions**, 8 metrics. No `cloudwatch:PutMetricData` anywhere. |
| D12 | Fan-out cap via the event source mapping's **`maximum_concurrency` = 5**; worker **reserved concurrency = 10** set in James's `dev.tfvars` once the limit is ≥ 110 (100 unreserved + 10; the project account's limit is 400, contract change 002). The root default is `null`, so a grader's fresh low-quota account can still apply; `terraform.tfvars.example` explains when to set it. |
| D13 | Packaging: **deterministic zips built outside Terraform** by `scripts/build_lambdas.py`; NumPy vendored in the worker zip from a hash-pinned `manylinux_2_28_x86_64` wheel; the API zip contains no NumPy; no layers, no containers. |
| D14 | Default sharding **10 × 100k** for a 1M-path job; chunk size **2¹⁷ = 131,072**; worker 1,024 MB; API 512 MB. |
| D15 | **Plain Monte Carlo** by default; **antithetic variates** as an opt-in request field (stretch, QMC-108). |
| D16 | SQS: standard queue, SSE-SQS, batch size 1, `ReportBatchItemFailures`, worker timeout **10 s**, visibility **60 s**, **`maxReceiveCount = 3`**, caught errors shorten visibility to **5 s**, main retention 1 day, DLQ retention 14 days. |
| D17 | Encryption: SSE-SQS on both queues, AWS-owned key on DynamoDB, `aws/ssm` for the SecureString, alerts SNS topic unencrypted (Budgets cannot publish to an AWS-managed-key topic). **No customer-managed keys.** |
| D18 | X-Ray **off** (its write actions need `Resource: "*"`). |
| D19 | **GitLab primary with GitLab CI** (runs on James's local Mac runner, so the `golden` job is emulated amd64). **GitHub is a plain second remote**, not a mirror: the integrator pushes `main` (and tags) to it at every gate; it is the submitted repository, and its GitHub Actions workflow (native x86) runs on each push. Changed 2026-10-13: no mirror token to manage, and a missed push is caught by the gate's sha check. |
| D20 | Terraform ≥ 1.10, AWS provider `~> 6.0`, **local state at `$HOME/.qmc/state/<env>.tfstate`** and settings at `$HOME/.qmc/<env>.tfvars` (both outside every worktree), five local modules (`data`, `api`, `workers`, `observability`, `budget`), `default_tags`. |
| D21 | Stretch in scope: **CloudWatch dashboard, Checkov in CI, per-job cost attribution, antithetic variates**, plus the replay diff report (core). Out: Step Functions, PENDING_ENTROPY, ephemeral environments, portfolio VaR, CI deploys. |
| D22 | Timeline: build Tue–Wed, gates as in §2. |
| D23 | Process (as on the telemetry project): WS0 freezes a contract, workstreams run in parallel git worktrees with path ownership, contract changes go through WS0, GitLab milestones are the gates. WS1 and WS5 start before G0. |
| D24 | Region **us-east-2**: the new-AWS-experience project is pinned there (contract change 001); Ohio is also near Qrypt's only documented endpoint, `api-eus` (East US). |
| D25 | AWS account on the **Paid plan**; the $0 claim rests on always-free allowances alone. |
| D26 | **Grader access under `AWS_IAM`:** Terraform creates an invoke-only IAM user `qmc-<env>-grader` (and a `qmc-<env>-client` role). Its access key is created **out-of-band by CLI** (so it never enters state) and sent privately to the program contact, never committed. |
| D27 | Job status: `QUEUED` (stored at submit), `RUNNING` (derived for display when some shards are done), `COMPLETE`, `PARTIAL`, `FAILED` (only when enqueueing failed after the job was recorded). A seeding failure writes **no** job. |
| D28 | Forced failure for the demo: request field `fault_injection.fail_shards`, accepted **only** when the deployment sets `enable_fault_injection = true` (true in James's `dev` tfvars, default false). |
| D29 | Job listing (`GET /jobs`) reads a `JOBLIST` index partition written at submit: no Scan, no GSI. |
| D30 | Every job result includes the **Black-Scholes reference**, the z-score and `within_3se`, computed by `qmc_core.bs`. |
| D31 | Validation limits (§5.2): paths ≤ 10,000,000; shards 1–50; paths per shard ≤ 1,000,000; all configurable by Terraform variables. |
| D32 | Job deadline for the reaper: **900 s** after submit (large enough for a burst backlog; the forced-failure demo uses last-attempt marking, not the reaper). |
| D33 | **Apply discipline.** Until G1, WS1 applies from its branch (rebased on `main`); from G1 on, every apply and destroy runs from the `main` checkout by the integrator, and `make apply` refuses a checkout whose `HEAD` does not contain `origin/main` unless `ALLOW_BRANCH_APPLY=1`. Each build writes `build/manifest.json` (git sha, branch, dirty flag); the sha is deployed as `QMC_BUILD_SHA` and shown by `GET /health`. |
| D34 | **G0 is split:** G0a (9:45 AM) puts interfaces, the final Makefile and the skeleton on `main`; G0 (noon) delivers implementations; the NumPy/SIMD pin follows by 2:00 PM (QMC-099). |
| D35 | **AI usage logs per workstream** in `docs/ai-log/WSn.md` (no merge conflicts); WS5 folds them into `AI_USAGE.md` at G4. |
| D42 | **`staging` integration branch** (James, 2026-10-13). Workstream MRs target `staging`; the integrator merges them, then promotes `staging` → `main` with one MR whose pipeline must pass, pushes `main` to GitHub, and applies from `main` (D33 unchanged). GitLab CI runs only for merge-request pipelines (and manual web runs), not plain pushes; GitHub Actions runs on push to the GitHub `main` (the only branch pushed there, D19). |

Spec conflicts resolved by these decisions:

- **"GitHub repository" vs our GitLab remote.** GitLab is primary; GitHub is a second remote, pushed at every gate, and is what we submit (D19).
- **"Terraform sets reserved concurrency on workers."** A new account's quota may make that impossible. A reservation needs a limit ≥ 110 (100 unreserved + 10); below that, Day 0 requests 1,000. The variable defaults to `null` for graders' fresh accounts; `maximum_concurrency` still caps fan-out, and README says so (D12).
- **"Submit API latency < 800 ms p95 (warm)."** We report warm p50/p95 over 50 submissions as specified, and cold-start latency separately.
- **"Write `{sum, sum_sq, n}` per shard."** Stored as `sum_hex`, `sum_sq_hex` (Python `float.hex()`) and `n` (integer), so bit-exactness is a string compare (§6.2).
- **Status names.** The assignment shows `QUEUED`, `COMPLETE` and `PARTIAL`; we add `RUNNING` (derived) and `FAILED` (enqueue failure only) (D27).
- **"A forced worker failure trips the DLQ alarm."** Forced through D28's flag; the DLQ is never consumed, so the alarm can fire (D9).
- **"Use SQS long polling."** The event source mapping long-polls; the queue also sets `receive_wait_time_seconds = 20`.
- **Function URL behind `AWS_IAM` vs "deployed endpoint URL kept up through grading."** Graders call it with the grader user's credentials and `clients/qmc.py` (D26).
- **"Terraform creates … CloudWatch custom metrics."** EMF metrics are created by the log lines, not by a Terraform resource. Terraform sets the namespace and metric settings (`METRICS_NAMESPACE`), and creates the alarms and the dashboard on those metrics. README and ARCHITECTURE say this explicitly, and the dashboard shows the four required metrics.
- **"No unhandled exceptions in handlers" vs the worker's init self-check.** The self-check raises at import *by design* (fail closed rather than produce a non-reproducible result); handler code never raises. README explains it.
- **`docs/ASSIGNMENT.md` has mis-encoded characters** (`Â·`, `â‰¤`): read them as `·`, `≤`, `≈`, `σ`.

## 4. Repository layout and ownership

```
pyproject.toml uv.lock       uv project: Python 3.13, dev dependencies (WS0)
Makefile                     setup fmt lint test test-golden build plan apply destroy outputs e2e bench burst
                             force-failure warm-dlq replay-check drift-check (WS0 writes the final recipes at G0a;
                             each calls a fixed script path that WS1 or WS4 fills in, so nobody else edits it)
.gitattributes               (WS0)
.env.example                 local settings for tools (WS0)
.gitlab-ci.yml               CI pipeline (WS0)
CLAUDE.md                    agent rules (WS0)
requirements/worker.txt      hash-pinned NumPy for the worker zip (WS0)
src/qmc_core/                pure shared package: params, seeds, sim, bs, aggregate, codec, ddb, messages,
                             errors, emf, logjson, fingerprint, replay, cost (WS0)
src/qmc_api/                 API router Lambda: routes, submit, status, replay, health, qrypt, secrets, limiter (WS2)
src/qmc_worker/              worker Lambda (WS3)
clients/qmc.py               SigV4 CLI client for graders and tools (WS2)
infra/                       Terraform root module + modules/{data,api,workers,observability,budget} (WS1)
scripts/build_lambdas.py     deterministic Lambda zips into build/ (WS1)
scripts/bootstrap.sh, scripts/*gitlab*   repo and GitLab automation (WS0)
tests/conftest.py            shared fixtures: moto table/queues/SSM, IAM allow-list guard (WS0)
tests/core/                  qmc_core unit tests (WS0)
tests/api/  tests/worker/    handler unit tests (WS2, WS3)
tests/correctness/           closed form, seed determinism, golden hash (WS4)
tests/integration/           in-process moto tests across api + worker (WS4)
tests/deployed/              tests against the live stack, marked `deployed` (WS4)
tools/                       e2e, bench, burst, force_failure, replay_many, drift_check, qrypt_probe (WS4)
docs/                        HANDOFF, PRD, ROADMAP, PRIMER, DECISIONS, prompts, contract-changes, results
docs/ai-log/WSn.md           per-workstream AI usage logs (each WS owns its own file)
README.md AI_USAGE.md        deliverables at the root (WS5)
```

Fixed script paths the Makefile calls: `build` → `uv run python scripts/build_lambdas.py`; `plan`/`apply`/`destroy` → `terraform -chdir=infra … -backend-config="path=$HOME/.qmc/state/$(ENV).tfstate" -var-file=$HOME/.qmc/$(ENV).tfvars $(EXTRA_VARS)` (apply/destroy guarded per D33); `outputs` → `terraform -chdir=infra output -json`; `test-golden` → Docker run of `public.ecr.aws/lambda/python:3.13` with `QMC_ENFORCE_PINNED_DISPATCH=true`; `e2e` → `tools/e2e.py`; `bench` → `tools/bench.py`; `burst` → `tools/burst.py`; `force-failure` → `tools/force_failure.py`; `warm-dlq` → `tools/warm_dlq.sh`; `replay-check` → `tools/replay_many.py`; `drift-check` → `tools/drift_check.sh`. Until the owner fills a script in, it prints `TODO <ticket>` and exits 1.

| Workstream | Branch | Worktree | Owns (only these paths may be edited) |
| --- | --- | --- | --- |
| WS0 Foundation and contract | `ws0-foundation` | main checkout | Root files except `README.md` and `AI_USAGE.md`; `src/qmc_core/**`, `tests/core/**`, `requirements/**`, `scripts/bootstrap.sh`, `scripts/*gitlab*`, `scripts/issue_map.csv`, `.gitlab-ci.yml`, `.github/workflows/**`, `docs/HANDOFF.md`, `docs/ROADMAP.md`, `docs/prompts/**`, `docs/contract-changes/**`, `docs/ai-log/WS0.md`, `CLAUDE.md` |
| WS1 Infrastructure | `ws1-infra` | `../quantum-seeded-monte-carlo-ws1` | `infra/**`, `scripts/build_lambdas.py`, `docs/ai-log/WS1.md` |
| WS2 API | `ws2-api` | `../quantum-seeded-monte-carlo-ws2` | `src/qmc_api/**`, `tests/api/**`, `clients/**`, `docs/ai-log/WS2.md` |
| WS3 Worker | `ws3-worker` | `../quantum-seeded-monte-carlo-ws3` | `src/qmc_worker/**`, `tests/worker/**`, `docs/ai-log/WS3.md` |
| WS4 Verification | `ws4-verify` | `../quantum-seeded-monte-carlo-ws4` | `tests/correctness/**`, `tests/integration/**`, `tests/deployed/**`, `tools/**`, `docs/TEST_RESULTS.md`, `docs/BENCHMARKS.md`, `docs/ai-log/WS4.md` |
| WS5 Docs and demo | `ws5-docs` | `../quantum-seeded-monte-carlo-ws5` | `README.md`, `AI_USAGE.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/COST.md`, `docs/SECURITY.md`, `docs/DEMO_SCRIPT.md`, `docs/PRIMER.md`, `docs/images/**`, `docs/ai-log/WS5.md` |

- **Interface changes go through WS0.** A new or changed DynamoDB attribute or key, SQS message field, API field or error code, environment variable, Terraform variable or output that another workstream reads, or any `qmc_core` change: write `docs/contract-changes/NNN-short-title.md` and tell James. Do not edit files you do not own.
- WS0 pre-declares every dev dependency at G0 (pytest, pytest-cov, ruff, moto[dynamodb,sqs,ssm], boto3, botocore, hypothesis, numpy==pinned), so others rarely touch `uv.lock`.
- Each workstream writes its AI usage log to `docs/ai-log/WSn.md` (its own file, so no merge conflicts); WS5 folds them into `AI_USAGE.md` at G4 (D35).
- WS1 owns `infra/.tflint.hcl`; WS0 owns the root `.gitattributes` and `scripts/issue_map.csv`.
- **Apply discipline (D33).** Only WS1 (until G1, from its branch rebased on `main`, announced to James) and the integrator (from the `main` checkout) run `make apply` or `make destroy`, one at a time; the local backend's lock file guards the shared state. After G1, every apply is from `main`. Settings come from `$HOME/.qmc/<env>.tfvars`, never from a checkout. WS4's tools read outputs with `make outputs` and never apply; WS4 writes `tools/drift_check.sh`, and the **integrator runs** `make drift-check` from `main` in the G2 window. Drills that need a configuration change (the fail-closed drill) are applied by the integrator with `make apply EXTRA_VARS='-var …'` and reverted by a normal `make apply`.

### 4.1 Interface seams fixed at G0a

The signatures and docstrings in `src/qmc_core/*.py` are the contract. In particular:

- **Errors.** Every §5.3 code is a `qmc_core.errors.ErrorCode` member whose value is `(http_status, default_message)`. `QmcError(code, message=None, details=None, retry_after=None)` has `to_body()`. `EntropyError` is a subclass used by `seeds`. `params.parse_body` and `params.validate_job_request` **raise** `QmcError` (`INVALID_JSON`, `BODY_TOO_LARGE`, `VALIDATION_ERROR` with every detail, then `UNSUPPORTED`, then `FAULT_INJECTION_DISABLED`). They do not return error lists.
- **Qrypt.** `seeds.parse_qrypt_response(status, body) -> bytes` (the 1,024-byte block). It maps every HTTP status and every body defect to its §5.3 code by raising `EntropyError`. WS2 owns transport errors (`QRYPT_TIMEOUT`, `QRYPT_UNREACHABLE`) and the `EntropyResult` provenance type in `qmc_api.qrypt`.
- **Entropy injection.** `FakeEntropySource` lives in `qmc_api.qrypt` (WS2). Tests inject a source with `qmc_api.qrypt.set_entropy_source(source)` (`None` restores `QryptHttpSource`). The submit path obtains its source only through `qmc_api.qrypt.get_entropy_source()`. WS2 implements both functions; WS4's integration tests use them.
- **When a second limiter slot is refused** before the one Qrypt retry, submit returns the original mapped Qrypt error (not `ENTROPY_RATE_LIMITED`), because the cause is Qrypt. `QryptLatencyMs` and a `qrypt_call` log line (§9.3) are emitted for **every** HTTP attempt, including failures and the retry.
- **DynamoDB builders** return kwargs for the boto3 **resource** `Table` methods (`table.update_item(**ddb.mark_done(job_id, i))`). The one exception is `job_statuses(table_name, ids)`, which is for `boto3.resource("dynamodb").batch_get_item`. `ddb.is_conditional_failure(exc)` and `ddb.item_from_error(exc)` (the `ALL_OLD` item, deserialized) handle refusals. `shard_put(job_id, i, result: ShardResult, *, duration_ms, memory_mb, attempt, fingerprint, now_epoch)`.
- **Decision.** `aggregate.decide(job_item, now_epoch=None) -> Decision(action, done, failed, add_failed, n_done_seen, reason)`, where `action` is `"none"`, `"complete"` or `"partial"`. The worker passes `now_epoch=None` (no reaping). The API passes the current time, so the deadline reaper (§7.7) is the same function. For `"partial"`: `aggregate(shards, only=decision.done)` → `ddb.finalize_partial(job_id, partial, decision.add_failed, decision.n_done_seen, now)`. On a conditional failure, re-decide once from `ddb.item_from_error(exc)` (worker) or a fresh consistent read (API).
- **`ShardResult`** lives in `qmc_core.aggregate` (NumPy-free) and is re-exported by `sim`.
- **Worker.** `ForcedFailure` is defined in `qmc_worker`. The worker reads `TABLE_NAME` and `QUEUE_URL` from the environment at call time (or caches them lazily on first use), never at import, so moto tests can set them per test. Poison messages get no visibility change (§7.6 step 1). `ShardFailures` is emitted only when `mark_failed` succeeds.
- **Concurrency tests.** moto's in-process backend is not guaranteed to be thread-safe. Prove races with deterministic interleavings (run step A of worker 1, then step B of worker 2, …), not threads.
- **Shared test fixtures** (`tests/conftest.py`, WS0): `aws_env`, `moto_aws`, `ddb_table` (resource Table `qmc-test`), `queues` (sets `QUEUE_URL`; redrive 3), `qrypt_param`, `enforce_off`, and `iam_guard`. Run handler code as `with iam_guard("api"|"worker") as calls: ...`. The guard records every botocore call and fails at block exit on any operation outside §8.3, even one the handler swallowed.
- **NumPy-free constants.** `ALGO_VERSION` (`qmc-sim/v1`) and `CHUNK_SIZE` (131,072) live in `qmc_core.params`; `qmc_core.sim` re-exports them (WS2 needs them without NumPy).
- **Limiter (contract change 006).** `ddb.limiter_gap_take(now, gap_ms)` returns the `update_item` kwargs; a `ConditionalCheckFailed` means the gap has not passed, and `ddb.limiter_gap_retry_after(ddb.item_from_error(exc), now, gap_ms)` gives `Retry-After`. The API reads `QRYPT_MIN_GAP_MS` (default 2000). The fixed-window builders (`limiter_window`, `limiter_take`) remain in `qmc_core` but are no longer used by the API; `QRYPT_WINDOW_S` and `QRYPT_MAX_PER_WINDOW` are removed.
- **JOBLIST items** carry no `created_at` attribute; read it from the `sk` (`<created_at>#<job_id>`).
- **Client seam (WS2 → WS4).** `clients.qmc.request(method, path, body=None, url=None) -> (status, headers, json_body)` returns on every HTTP status and resolves the URL like the CLI; the CLI commands use it. `FakeEntropySource(seed=b"qmc-fake-entropy", failures=())` (`failures`: `ErrorCode`, `(status, body)` or `None`, consumed one per request; `.calls` records them).
- **Drift and `build_sha`.** Every commit changes `QMC_BUILD_SHA`, so a `make plan` after a new commit shows two in-place Lambda updates by design. "No changes" is checked at the same sha as the last apply (WS4's `drift_check.sh`).
- **Make arguments.** `make e2e ARGS=--fresh`; `make replay-check N=20` calls `tools/replay_many.py --n 20`. Every tool target passes `$(ARGS)`.

## 5. API contract (WS2)

### 5.1 Transport

One Function URL on the `qmc-<env>-api` function, `authorization_type = "AWS_IAM"`, invoke mode `BUFFERED`, no CORS. Requests arrive in payload format 2.0: route on `event["requestContext"]["http"]["method"]` and `event["rawPath"]` (`pathParameters` is null); decode the body with `base64` when `isBase64Encoded`. Every response is JSON (`content-type: application/json`), and every response carries `x-request-id` (the Lambda request id).

| Method and path | Purpose | Success |
| --- | --- | --- |
| `POST /jobs` | Validate, seed from Qrypt, record, enqueue | **202** `{"job_id", "status": "QUEUED", "paths", "shards", "master_seed_hex", "links": {"self": "/jobs/<id>", "replay": "/jobs/<id>/replay"}}` |
| `GET /jobs` | Newest 50 jobs from `JOBLIST` | 200 `{"jobs": [{"job_id","status","created_at","paths","shards","replay_of"}]}`; a fresh deployment returns `{"jobs": []}` |
| `GET /jobs/{id}` | Job view (§5.4); runs lazy finalize and the deadline reaper first (§7.7) | 200 |
| `POST /jobs/{id}/replay` | New job with the original's master seed, parameters and sharding; no Qrypt call | **202** `{"job_id": "<new>", "replay_of": "<id>", "status": "QUEUED", ...}` |
| `GET /health` | DynamoDB `GetItem` on `HEALTH/HEALTH` and SSM `GetParameter` (no decryption) on the token parameter | 200 `{"status":"ok","checks":{"dynamodb":"ok","ssm":"ok"},"build_sha":"<QMC_BUILD_SHA>","env":"dev"}`, else **503** naming the failing check |

Job ids are `uuid4().hex` (32 lowercase hex); an id that is not 32 lowercase hex characters is simply not found (404 `JOB_NOT_FOUND`), never a lookup error. Unknown path → 404 `ROUTE_NOT_FOUND`; known path, wrong method → 405 `METHOD_NOT_ALLOWED`. `GET /jobs` reads the newest 50 `JOBLIST` entries, then their current statuses with one `BatchGetItem` (projection `job_id, #s`).

### 5.2 Request validation (`qmc_core.params.validate_job_request`)

The body must be a JSON object of at most 16 KiB (not JSON → 400 `INVALID_JSON`; JSON but not an object, e.g. an array → 400 `INVALID_JSON` with message "body must be a JSON object"). Every rule failure is collected and returned together as one `VALIDATION_ERROR`; unknown fields are reported in the same `details` list with `problem: "unknown field"`.

| Field | Rule |
| --- | --- |
| `S0`, `K` | JSON number (not boolean, not string), finite, `0 < x ≤ 1e6` |
| `r` | JSON number, finite, `-1 ≤ r ≤ 1` |
| `sigma` | JSON number, finite, `0 < sigma ≤ 5` |
| `T` | JSON number, finite, `0 < T ≤ 50` (years) |
| `paths` | JSON **integer** (not `1e6`, not `1000000.0`, not boolean), `2 ≤ paths ≤ MAX_PATHS` (10,000,000) |
| `shards` | JSON integer, `1 ≤ shards ≤ MAX_SHARDS` (50), `shards ≤ paths` |
| ratio | `ceil(paths / shards) ≤ MAX_PATHS_PER_SHARD` (1,000,000), so a worker always finishes far inside its timeout |
| `variance_reduction` | optional, `"none"` (default) or `"antithetic"`; antithetic requires even `paths ≥ 4` (until QMC-108 lands, `"antithetic"` is rejected with `UNSUPPORTED`) |
| `fault_injection` | optional `{"fail_shards": [int, ...]}`, each in `[0, shards)`; rejected with `FAULT_INJECTION_DISABLED` unless the deployment enables it (D28) |
| anything else | a `VALIDATION_ERROR` detail with `problem: "unknown field"` |

Shard path split (`qmc_core.params.split_paths`): shard *i* gets `paths // shards`, plus one if `i < paths % shards`. For antithetic, the same split applies to pairs (`paths // 2`) and each shard simulates `2 × pairs_i` paths.

### 5.3 Errors

Body: `{"error": {"code": "<CODE>", "message": "<one sentence for a human>", "details": [{"field": "...", "problem": "..."}]}}`. `details` is present for validation errors only.

| HTTP | Code | When |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Any §5.2 rule (details list every failed field) |
| 400 | `INVALID_JSON` / `BODY_TOO_LARGE` / `UNSUPPORTED` / `FAULT_INJECTION_DISABLED` | As named (unknown fields are `VALIDATION_ERROR` details) |
| 404 | `JOB_NOT_FOUND` / `ROUTE_NOT_FOUND` | Unknown job id (message names the id) / unknown route |
| 405 | `METHOD_NOT_ALLOWED` | |
| 409 | `JOB_NOT_REPLAYABLE` | Replay of a job that is not `COMPLETE` |
| 429 | `ENTROPY_RATE_LIMITED` | Our limiter's window is full; `Retry-After` = seconds to the next window |
| 503 | `QRYPT_AUTH_FAILED` | Qrypt 401 (no or malformed `Authorization` header; Qrypt answers a present-but-wrong token with 403) |
| 503 | `QRYPT_FORBIDDEN` | Qrypt 403 `{"error": "Forbidden or entropy quota reached"}`: the token is invalid or expired, **or** the monthly allotment is used up. Qrypt sends the same status and body for both, so the message names both causes (contract change 003) |
| 503 | `QRYPT_RATE_LIMITED` | Qrypt 429 after the one allowed retry; `Retry-After: 10` |
| 503 | `QRYPT_SERVER_ERROR` | Qrypt 500 or another 5xx |
| 503 | `QRYPT_ENTROPY_UNAVAILABLE` | Qrypt 503 |
| 503 | `QRYPT_TIMEOUT` / `QRYPT_UNREACHABLE` | Deadline exceeded / DNS, TLS or connection failure |
| 502 | `QRYPT_BAD_RESPONSE` | Not JSON, `random` missing or not a list of exactly one string, invalid base64, decoded length ≠ 1,024, all-zero block, or a block already used (§7.5). A missing `size` is accepted and unknown keys are ignored (Qrypt's docs only say `size` "should" match); a present `size` other than 1 is rejected |
| 500 | `QRYPT_BAD_REQUEST` | Qrypt 400 (our bug) |
| 503 | `SECRET_UNAVAILABLE` | SSM read failed |
| 503 | `ENQUEUE_FAILED` | SQS send failed after retries; the job is marked `FAILED` |
| 500 | `INTERNAL_ERROR` | Anything unexpected (logged with the request id; never a stack trace in the body) |

Handlers never raise: every exception becomes one of these responses.

### 5.4 Job view (`GET /jobs/{id}`)

```json
{
  "job_id": "…", "status": "COMPLETE", "created_at": "2026-10-13T20:31:07.412Z", "completed_at": "…",
  "params": {"S0": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0},
  "paths": 1000000, "shards": 10, "variance_reduction": "none", "chunk_size": 131072,
  "progress": {"done": 10, "failed": 0, "total": 10},
  "master_seed_hex": "…64 hex…",
  "seed_provenance": {"source": "qrypt", "endpoint": "api-eus", "fetched_at": "…", "latency_ms": 212,
                      "http_status": 200, "block_bytes": 1024, "raw_sha256": "…", "kdf": "HKDF-SHA256",
                      "kdf_version": "qmc/master-seed/v1"},
  "result": {"estimate": 10.4459…, "std_error": 0.01471…, "ci95": [10.41…, 10.47…], "n": 1000000,
             "estimate_hex": "0x1.4e…p+3", "std_error_hex": "…", "ci95_hex": ["…", "…"]},
  "reference": {"black_scholes": 10.450583572185565, "z_score": -0.32, "within_3se": true},
  "failed_shards": [], "partial_result": null,
  "replay_of": null, "replay_check": null,
  "fingerprint": {"python": "3.13.x", "numpy": "2.x.y", "machine": "x86_64", "libc": "glibc 2.34",
                  "exp_target": "AVX2", "algo_version": "qmc-sim/v1", "bitgen": "PCG64DXSM", "chunk_size": 131072},
  "cost_estimate": null
}
```

- `RUNNING` is displayed when the stored status is `QUEUED` and at least one shard is done or failed.
- `PARTIAL` jobs have `result: null` and `partial_result` (the same shape, over the shards in `done_shards` only, labelled as such) plus `failed_shards` (displayed as `failed − done`, sorted). With fewer than 2 samples, `std_error` and `ci95` are null; with no done shard, `partial_result` is null (§7.4).
- For a replay, `replay_check` is `{"original_job_id", "bit_identical": bool, "estimate_equal": bool, "shards_compared": n, "shard_diffs": [{"shard_index", "field", "original_hex", "replay_hex"}], "fingerprint_match": bool, "fingerprint_diffs": {...}}`, computed on read by `qmc_core.replay.compare`.
- `fingerprint` is the shard fingerprint when every shard agrees; otherwise `{"mixed": true, "by_shard": [...]}`. `cpu_model` is reported per shard but excluded from `fingerprint_match` (different CPUs producing the same bits is the point).
- `cost_estimate` (stretch, QMC-126) is `{"lambda_gb_seconds", "lambda_requests", "sqs_requests", "dynamodb_write_units", "usd_at_list_price", "usd_after_free_tier": 0.0}`.

### 5.5 Client (`clients/qmc.py`, WS2)

Standard library plus `botocore` (SigV4, service `lambda`, region from the URL). Credentials from the normal AWS chain (profile, env, the grader user's keys). Commands: `submit` (flags or a JSON file), `get`, `wait` (poll every 2 s until a terminal status, print every status change and the elapsed time), `replay`, `list`, `health`. `--url` defaults to `$QMC_API_URL` or `terraform output -raw api_url`. Output is the JSON response, pretty-printed; exit code non-zero on HTTP ≥ 400.

## 6. Data model (WS0 defines in `qmc_core.ddb`; WS1 creates the table)

### 6.1 Table

`qmc-<env>`: partition key `pk` (S), sort key `sk` (S), `PROVISIONED` 10/10, TTL attribute `ttl`, no GSI, no streams. Every write goes through a builder in `qmc_core.ddb` that returns the exact `boto3` keyword arguments, so the expression text has one implementation and is unit-tested.

| Item | `pk` | `sk` | Attributes |
| --- | --- | --- | --- |
| Job | `JOB#<job_id>` | `META` | `job_id`, `status`, `created_at` (ISO, ms, `Z`), `completed_at`?, `params` (map of **strings**, each `repr(float)`), `paths`, `shards` (N), `paths_per_shard` (list of N), `chunk_size`, `variance_reduction`, `algo_version` (written at submit, copied by replay), `master_seed_hex`, `seed_provenance` (map), `replay_of`?, `deadline_at` (epoch s), `done_shards` (NS, absent until the first ADD; sets cannot be empty), `failed_shards` (NS, absent unless a shard failed), `result` (map of hex strings + `n`), `fail_reason`?, `fault_injection`?, `ttl` (created + 365 days) |
| Shard | `JOB#<job_id>` | `SHARD#<iiii>` (4-digit zero-padded, so a Query returns index order) | `shard_index`, `n`, `sum_hex`, `sum_sq_hex`, `duration_ms`, `memory_mb`, `attempt` (`ApproximateReceiveCount`), `fingerprint` (map incl. `cpu_model`), `completed_at`, `ttl` |
| Job list entry | `JOBLIST` | `<created_at>#<job_id>` | `job_id`, `status_at_submit`, `paths`, `shards`, `replay_of`?, `ttl` (list status is read from the job item) |
| Entropy block | `ENTROPY#<raw_sha256>` | `ENTROPY` | `job_id`, `fetched_at`, `ttl` (365 days). Conditional put = no block is used twice |
| Rate-limit gap | `RL#qrypt` | `LAST` | `at_ms` (N, epoch ms of the last Qrypt call), `ttl` (+ 3,600 s). (The fixed-window items `RL#qrypt/<window_start>` are no longer written, contract change 006.) |
| Health sentinel | `HEALTH` | `HEALTH` | never written; `GetItem` proves reachability |

Store floats **only** as `float.hex()` strings (shard sums, results) or `repr()` strings (parameters; normalise JSON integers first with `repr(float(x))`). Never pass a Python `float` to boto3 (it refuses) and never `Decimal(float)`.

### 6.2 Exact write expressions

Every attribute name in every expression goes through `ExpressionAttributeNames` (`#s`, `#n`, `#ttl`, `#result`, `#done`, `#failed`, …): `status`, `ttl`, `result`, `size` and `partial` are DynamoDB reserved words.

| Builder | Operation |
| --- | --- |
| `limiter_gap_take(now, gap_ms)` | `UpdateItem` `RL#qrypt/LAST`: `SET #at = :now, #ttl = :ttl` (epoch ms), condition `attribute_not_exists(#at) OR #at <= :cutoff` (`now_ms - gap_ms`), `ReturnValuesOnConditionCheckFailure=ALL_OLD` (contract change 006; used by the API) |
| `limiter_take(window, cap, now, size_s)` (unused since 006) | `UpdateItem` `RL#qrypt/<window>`: `ADD #n :one SET #ttl = :ttl`, condition `attribute_not_exists(#n) OR #n < :cap` |
| `entropy_claim(raw_sha256, job_id)` | `PutItem`, condition `attribute_not_exists(pk)` |
| `job_put(job)` | `PutItem`, condition `attribute_not_exists(pk)` |
| `joblist_put(job)` | `PutItem` |
| `shard_put(job_id, i, result)` | `PutItem`, condition `attribute_not_exists(pk)` |
| `mark_done(job_id, i)` | `UpdateItem`: `ADD #done :i_set`, condition `attribute_exists(pk)`, `ReturnValues=ALL_NEW` (a conditional failure means the job item is gone: log `orphan_shard` and treat the message as processed) |
| `mark_failed(job_id, i)` | `UpdateItem`: `ADD #failed :i_set`, condition `attribute_exists(pk) AND (attribute_not_exists(#done) OR NOT contains(#done, :i))`, `ReturnValues=ALL_NEW`, `ReturnValuesOnConditionCheckFailure=ALL_OLD` (so a refusal returns the item and the worker never needs `GetItem`). A shard already counted done is never marked failed |
| `finalize_complete(job_id, result, now)` | `UpdateItem`: `SET #s = :complete, #result = :r, completed_at = :now`, condition `#s = :queued AND attribute_not_exists(#failed)` |
| `finalize_partial(job_id, partial, failed, n_done_seen, now)` | `UpdateItem`: `SET #s = :partial, partial_result = :p, completed_at = :now ADD #failed :failed` (omit the `ADD` clause when `failed` is empty: sets cannot be empty), condition `#s = :queued AND size(#done) = :n_done_seen` (or `attribute_not_exists(#done)` when 0 were seen). The snapshot condition stops a PARTIAL computed from a stale read from landing after another shard completed. Both finalize builders set `ReturnValuesOnConditionCheckFailure=ALL_OLD`, so a refusal returns the current item to re-decide from once |
| `mark_enqueue_failed(job_id, reason)` | `UpdateItem`: `SET #s = :failed, fail_reason = :why`, condition `#s = :queued` |
| `shards_query(job_id)` | `Query` `pk = JOB#id AND begins_with(sk, "SHARD#")`, `ConsistentRead=True` |
| `job_get(job_id)` | `GetItem` `JOB#id/META`, `ConsistentRead=True` (API only) |
| `job_statuses(job_ids)` | `BatchGetItem`, projection `job_id, #s` (API only, for `GET /jobs`) |

A `ConditionalCheckFailedException` from `shard_put` means a duplicate delivery; from a finalize, that someone else finalized first. Neither is an error.

### 6.3 SQS message v1 (`qmc_core.messages`)

Queue `qmc-<env>-shards`; one message per shard; body is compact JSON:

```json
{"v": 1, "job_id": "…", "shard_index": 3, "n_shards": 10, "shard_paths": 100000,
 "params": {"S0": "100.0", "K": "100.0", "r": "0.05", "sigma": "0.2", "T": "1.0"},
 "master_seed_hex": "…", "chunk_size": 131072, "variance_reduction": "none",
 "algo_version": "qmc-sim/v1", "fail": false}
```

`shard_paths` is this shard's path count (pairs × 2 for antithetic), never the job total. `fail` is true only for shards listed in an accepted `fault_injection`. The submit Lambda sends with `SendMessageBatch` (10 per call), retrying failed entries twice. The message carries everything the worker needs, so the worker never reads the job item before simulating.

## 7. Semantics

Every rule here has exactly one implementation, in `qmc_core`, used by the API, the worker and the tests alike.

### 7.1 Master seed (`qmc_core.seeds.derive_master_seed`)

- Input: the decoded 1,024-byte block and the job id. Reject (raise `EntropyError`) if the length is not 1,024 or every byte is zero.
- `prk = HMAC-SHA256(key=b"qmc/hkdf-salt/v1", msg=block)`; `okm = HKDF-Expand(prk, info=b"qmc/master-seed/v1|" + job_id.encode("ascii"), L=32)` (RFC 5869, standard library `hmac` + `hashlib`, tested against RFC 5869 test case 1).
- `master_seed_hex = okm.hex()` (64 lowercase hex); `master_int = int.from_bytes(okm, "big")`.
- Provenance stored with the job: `raw_sha256 = sha256(block).hexdigest()`, KDF name and version, fetch time, latency, HTTP status. The raw block itself is not stored or logged.
- Never mix in local entropy (`os.urandom`, `secrets`, time). This module imports none of `os`, `random`, `secrets`, `time` (enforced by a test).

### 7.2 Shard streams (`qmc_core.seeds.shard_generator`)

`np.random.Generator(np.random.PCG64DXSM(np.random.SeedSequence(master_int, spawn_key=(shard_index,))))`. This equals `SeedSequence(master_int).spawn(n)[i]` (WS0 proves it in a test), so a worker needs only its index. `pool_size` stays at the default 4; the 256-bit claim applies to the stored seed, the RNG's internal state is 128-bit (README says so).

### 7.3 Simulation (`qmc_core.sim.simulate_shard`, version `qmc-sim/v1`)

All arithmetic in float64, in this exact order:

```
drift = (r - 0.5 * sigma * sigma) * T            # Python floats
vol   = sigma * math.sqrt(T)
disc  = math.exp(-r * T)
for each chunk of m = min(chunk_size, remaining) paths, in order:
    z      = rng.standard_normal(m)
    s_t    = S0 * np.exp(drift + vol * z)
    x      = disc * np.maximum(s_t - K, 0.0)
    sums.append(float(np.sum(x)));  sumsq.append(float(np.sum(x * x)))
sum = math.fsum(sums); sum_sq = math.fsum(sumsq); n = paths
```

Antithetic (`qmc-sim/v1`, `variance_reduction = "antithetic"`): per chunk of `m` pairs draw `z`; `z_minus = -z`; `x_plus = disc * np.maximum(S0 * np.exp(drift + vol * z) - K, 0.0)`; `x_minus = disc * np.maximum(S0 * np.exp(drift + vol * z_minus) - K, 0.0)`; `x = 0.5 * (x_plus + x_minus)`; then the same two sums; `n` counts pairs. Never use `@`, `np.dot` or anything BLAS-backed in this path (OpenBLAS picks kernels by CPU). Never change the chunk size for an existing `algo_version`.

`qmc_core.bs.black_scholes_call(S0, K, r, sigma, T)` uses `math.erf` (`N(x) = 0.5 * (1 + erf(x / sqrt(2)))`); for the reference case it returns 10.450583572185565 (±1e-12).

### 7.4 Aggregation (`qmc_core.aggregate`)

Over the shards in **index order** (for a partial result, the shards in `done_shards` only): `N = Σ n` (integer). If `N = 0` the result is `null`. If `N = 1`, `estimate` is `S` and `std_error`/`ci95` are `null` (never a division by zero). Otherwise `S = math.fsum(sums)`, `Q = math.fsum(sum_sqs)`, `mean = S / N`, `var = max(0.0, (Q - S * mean) / (N - 1))`, `se = math.sqrt(var / N)`, `ci95 = (mean - 1.959963984540054 * se, mean + 1.959963984540054 * se)`. Results are stored as hex. For antithetic jobs the same formulas apply with pairs as the sample unit. This function is the only place a job result is computed; the worker's finalize and the API's lazy finalize both call it.

### 7.5 Submit (`POST /jobs`, WS2)

1. Parse and validate (§5.2). Failure → 400; nothing else happens.
2. Generate `job_id`. Take the limiter slot (§6.2, `limiter_gap_take(now, QRYPT_MIN_GAP_MS)`, 2,000 ms). Window full → 429 `ENTROPY_RATE_LIMITED`.
3. Read the token from SSM (§8.4). Failure → 503 `SECRET_UNAVAILABLE`.
   (Steps 2–5 write nothing except the limiter slot.)
4. `GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1` with `Accept: application/json`, `Authorization: Bearer <token>`, over a module-level `http.client.HTTPSConnection` reused across warm invocations, with an **overall deadline of 2,500 ms** (connect and read timeouts derived from the time left, and never more than `context.get_remaining_time_in_millis() - 500`). Parse with `qmc_core.seeds.parse_qrypt_response` (strict base64, one block, 1,024 bytes, not all-zero). Map failures per §5.3. Allow **at most one** retry, only for 5xx or a network error (never for 429, contract change 006), only if less than 1,000 ms have elapsed, after a 100–300 ms jittered pause, and only after taking another limiter slot. Never retry 400, 401 or 403. A reused keep-alive connection that the server closed while idle (connection reset or `RemoteDisconnected` before any response byte) is reopened once immediately; that reconnect is not the retry and needs no extra slot.
5. `entropy_claim` (conditional). Already used → 502 `QRYPT_BAD_RESPONSE`.
6. Derive the master seed (§7.1). `job_put`, then `joblist_put`.
7. Enqueue all shard messages. If any still fail after two retries → `mark_enqueue_failed`, 503 `ENQUEUE_FAILED`. Any other failure after `job_put` succeeded (for example `joblist_put` throttled out) also runs `mark_enqueue_failed` and returns 503, so no job is left `QUEUED` without messages.
8. Emit EMF `JobsSubmitted = 1`, `EntropyKiB = 1`, `QryptLatencyMs`. Return 202.

On any Qrypt or secret failure, emit `SeedingFailures = 1`, write nothing, enqueue nothing, and return the mapped error. The token, the `Authorization` header and the raw block never appear in a log line.

The entropy source is an interface (`EntropySource.fetch() -> EntropyResult`) with two implementations in `qmc_api.qrypt`: `QryptHttpSource` and `FakeEntropySource` (deterministic blocks from SHAKE-256 of a test seed and a counter, with injectable failures for every §5.3 code). Tests never need a token.

### 7.6 Worker (WS3)

Event source mapping batch size 1. For the record:

1. Decode the message (`qmc_core.messages.decode`); malformed → log `poison_message`, report the item failure (it reaches the DLQ after 3 receives; there is no job to mark).
2. If `msg.fail` and `FAULT_INJECTION_ENABLED` → raise `ForcedFailure`.
3. Simulate (§7.3) and time it.
4. `shard_put`. `ConditionalCheckFailed` → log `duplicate_delivery` and **continue** (never overwrite, never skip the next steps: an earlier attempt may have crashed after the put).
5. `mark_done` → the job's `done_shards`, `failed_shards`, `shards`, `status` (strongly consistent: `ReturnValues` reflects the write). A conditional failure means the job item is gone: log `orphan_shard` and treat the record as processed.
6. If `|done ∪ failed| = shards` and `status = QUEUED` → **finalize**: `shards_query`, aggregate (§7.4), then `finalize_complete` (no failures) or `finalize_partial` (with the `done` count just seen). `ConditionalCheckFailed` on `finalize_complete` → someone else finalized; fine. On `finalize_partial` → take the current item from the exception (`ReturnValuesOnConditionCheckFailure=ALL_OLD`; the worker never calls `GetItem`) and re-decide once.
7. Emit EMF `PathsSimulated = n` (only when the put succeeded) and, if finalized, `JobsCompleted` or `JobsPartial`.

On any exception in steps 2–6:

- In every caught case, first `ChangeMessageVisibility(…, RETRY_VISIBILITY_S)` (5 s), so the next receive comes quickly (SQS moves a message to the DLQ on the receive *after* the `maxReceiveCount`-th, so this also applies on the last attempt). A forced failure therefore reaches the DLQ in about 15–20 s.
- If `ApproximateReceiveCount ≥ MAX_RECEIVE_COUNT` (3): `mark_failed(job, i)`; if that makes `|done ∪ failed| = shards`, finalize PARTIAL; emit `ShardFailures = 1`; report the item failure (the message goes to the DLQ). If `mark_failed`'s condition fails because the shard is already done, the failure was after counting: retry the finalize once and still report the item failure.
- Partial results are aggregated over the shards in `done_shards` only.
- Otherwise report the item failure; the retry comes in about 5 s.

Crashes, out-of-memory and timeouts skip this handling; they retry after the 60 s visibility timeout and, on the last attempt, are caught by the reaper (§7.7).

### 7.7 Lazy finalize and the deadline reaper (on `GET /jobs/{id}`, WS2)

Read the job with `ConsistentRead=True`. Before rendering a `QUEUED` job: if `|done ∪ failed| = shards`, run the same finalize as §7.6 step 6. Else if `now > deadline_at` (`created_at + JOB_DEADLINE_S`, 900 s), `finalize_partial` with `failed = all indexes − done` and the snapshot condition `size(done_shards) = <count read>`; if it fails, re-read and re-decide once (the job may have just completed). All writes are conditional on `status = QUEUED`, so the worker and the API can race safely. The decision itself is `qmc_core.aggregate.decide()`, shared with the worker. A late shard that lands after a reaper decision updates `done_shards` but cannot change the status: PARTIAL is final (D9).

### 7.8 Replay (`POST /jobs/{id}/replay`, WS2)

The original must exist (404) and be `COMPLETE` (409). Create a new job with a new id, `replay_of = <id>`, the original's `master_seed_hex`, `params`, `paths_per_shard`, `chunk_size`, `variance_reduction` and `algo_version`, and `seed_provenance = {"source": "replay", "replay_of": "<id>"}`. No limiter, no Qrypt call, no entropy claim; `fault_injection` is not copied. Enqueue as in §7.5 step 7. `GET` on the replay computes `replay_check` (§5.4) once both are terminal.

### 7.9 Runtime fingerprint and the SIMD self-check (`qmc_core.fingerprint`)

At worker init (module import), before any work:

- `NPY_DISABLE_CPU_FEATURES` is set by Terraform (value pinned by WS0 for the exact NumPy version, QMC-099; research value `"X86_V4 AVX512_ICL AVX512_SPR"`).
- `numpy.lib.introspect.opt_func_info(func_name="exp", signature="float64")` reports the dispatched target. If it is any AVX-512-family target (`AVX512*`, `X86_V4`) **and `QMC_ENFORCE_PINNED_DISPATCH=true`**, **raise at init**: every invocation then fails, messages reach the DLQ and the alarms fire. Better no result than a non-reproducible one. Terraform sets the flag on the worker; CI's `golden` job and `make test-golden` set it; everywhere else (laptops, CI's unit job on an AVX-512 runner) the check logs a warning and the fingerprint records `"dispatch_enforced": false`, so worker tests can import the handler anywhere.
- Fingerprint: `python` (`platform.python_version()`), `numpy`, `machine`, `libc` (`platform.libc_ver()`), `exp_target`, `algo_version`, `bitgen`, `chunk_size`, `dispatch_enforced`, the CPU flags `fma`, `avx2`, `avx512f` (glibc picks its `exp` variant by CPU feature, so `fma` is part of `fingerprint_match`), and `cpu_model` (from `/proc/cpuinfo`, informational, excluded from the match).
- The worker's runtime is pinned with `update_runtime_on = "FunctionUpdate"` (AWS provider 6.68 has no `runtime_management_config` block on `aws_lambda_function`; WS1 uses the separate `aws_lambda_runtime_management_config` resource, verified after the first apply), so AWS cannot change glibc underneath a deployed function mid-grading.

### 7.10 Cost attribution (stretch, QMC-126, `qmc_core.cost`)

From the shard items: GB-seconds = Σ `duration_ms / 1000 × memory_mb / 1024`, plus an API allowance; requests = 1 submit + *n* workers + polls observed; SQS requests = ⌈n/10⌉ sends + *n* receives + *n* deletes; DynamoDB write units ≈ 5 + 2*n* + 1. Priced at us-east-2 list prices as constants with their source URLs, and `usd_after_free_tier: 0.0` with a note.

## 8. Infrastructure (WS1, `infra/`)

### 8.1 Layout and conventions

```
infra/
  versions.tf      terraform >= 1.10; aws ~> 6.0; archive not needed (zips come from scripts/build_lambdas.py)
  providers.tf     region = var.region; default_tags { Project = "qmc", Env = var.env, ManagedBy = "terraform" }
  main.tf          module "data", "workers", "api", "observability", "budget"
  variables.tf     every variable documented (description, type, default, validation where sensible)
  outputs.tf       api_url, table_name, queue_url, dlq_url, worker_function, api_function, client_role_arn,
                   grader_user_name, alerts_topic_arn, log_groups
  terraform.tfvars.example
  modules/<name>/{main.tf,variables.tf,outputs.tf,README.md}
```

- Backend: `backend "local" {}` with the path supplied by the Makefile: `terraform -chdir=infra init -backend-config="path=$HOME/.qmc/state/$(ENV).tfstate"`; plan, apply and destroy add `-var-file=$HOME/.qmc/$(ENV).tfvars`. Neither state nor settings ever live in a worktree (D33). Commit `.terraform.lock.hcl` with `darwin_arm64` and `linux_amd64` hashes.
- Names: `qmc-${env}` prefix; `env` defaults to `dev`.
- IAM documents are built with `aws_iam_policy_document`; **no `*` in any action or resource**, except the `:*` log-stream suffix on the function's own log group ARN, which the README explains.
- The SSM parameter is referenced by name only: ARN built as `arn:aws:ssm:${region}:${account_id}:parameter${var.qrypt_api_key_param_name}` with `data "aws_caller_identity"`. **Never** `data "aws_ssm_parameter"` (it writes the decrypted value into state).
- Every Lambda log group is a Terraform resource with `retention_in_days = 14`, created before its function (`depends_on`), and no role gets `logs:CreateLogGroup`.

### 8.2 Modules and resources

| Module | Resources and settings |
| --- | --- |
| `data` | `aws_sqs_queue_policy` on both queues denying any `sqs:*` call with `aws:SecureTransport = false` (TLS in transit, cited in SECURITY.md; a Deny with `Principal: "*"` is the standard pattern, not a grant). `aws_dynamodb_table` `qmc-<env>` (§6.1; `billing_mode = "PROVISIONED"`, `read_capacity = var.table_rcu` (10), `write_capacity = var.table_wcu` (10), `ttl { attribute_name = "ttl", enabled = true }`, `point_in_time_recovery { enabled = false }`, `deletion_protection_enabled = false`, **no** `server_side_encryption` block). `aws_sqs_queue` `qmc-<env>-shards` (visibility 60, retention 86,400, `receive_wait_time_seconds = 20`, `sqs_managed_sse_enabled = true`) and `qmc-<env>-shards-dlq` (retention 1,209,600, SSE on). `aws_sqs_queue_redrive_policy` with `maxReceiveCount = var.max_receive_count` (3, an integer in `jsonencode`) |
| `workers` | `aws_cloudwatch_log_group` `/aws/lambda/qmc-<env>-worker`; IAM role + inline policy (§8.3); `aws_lambda_function` `qmc-<env>-worker` (`python3.13`, `architectures = ["x86_64"]`, handler `qmc_worker.handler.handle`, memory 1,024, timeout 10, `filename = var.worker_zip`, `source_code_hash = filebase64sha256(var.worker_zip)`, `reserved_concurrent_executions = var.worker_reserved_concurrency`, `aws_lambda_runtime_management_config` with `update_runtime_on = "FunctionUpdate"`); `aws_lambda_event_source_mapping` (`batch_size = 1`, `function_response_types = ["ReportBatchItemFailures"]`, `scaling_config { maximum_concurrency = var.worker_max_concurrency }` (5), `depends_on` the role policy so IAM has propagated) |
| `api` | Log group `/aws/lambda/qmc-<env>-api`; role + policy; `aws_lambda_function` `qmc-<env>-api` (`python3.13`, x86_64, handler `qmc_api.handler.handle`, memory 512, timeout 10, no reserved concurrency (0 would disable the URL)); `aws_lambda_function_url` (`authorization_type = "AWS_IAM"`, `invoke_mode = "BUFFERED"`); `aws_iam_role` `qmc-<env>-client` (trust: this account's root) and, when `var.create_grader_user`, `aws_iam_user` `qmc-<env>-grader` with `force_destroy = true` (its access key is created by CLI, D26), both with the invoke policy (§8.3) |
| `observability` | `aws_sns_topic` `qmc-<env>-alerts` (unencrypted, D17) with a topic policy allowing the account, `cloudwatch.amazonaws.com` and `budgets.amazonaws.com` (each with `aws:SourceAccount`) to publish; `aws_sns_topic_subscription` email to `var.alert_email`; the alarms in §9.2 with `alarm_actions` and `ok_actions` on the topic; dashboard `qmc-<env>` (stretch, QMC-118) |
| `budget` | `aws_budgets_budget` `qmc-monthly` (`budget_type = "COST"`, `limit_amount = "1.0"`, `limit_unit = "USD"`, `time_unit = "MONTHLY"`, no `time_period_start`, never `timestamp()`, `depends_on` the SNS topic policy because Budgets validates publish permission at creation), notifications: ACTUAL > 1% (any spend), ACTUAL > 100%, FORECASTED > 100%, each to the SNS topic and `var.alert_email`. `count = var.create_budget ? 1 : 0` (budgets are account-wide; only one environment creates it) |

### 8.3 IAM, one role per function (no wildcards)

**SCP constraint (found at the first apply).** The new-AWS-experience Paid-plan SCP `DenyIAMRestrictedActions` denies `iam:CreateGroup`, `iam:PutGroupPolicy` and `iam:AddUserToGroup`. So the grader user's invoke policy is an **inline user policy**, not a group (Checkov `CKV_AWS_40` is skipped with this reason). `iam:CreateUser`, `iam:PutUserPolicy` and `iam:CreateAccessKey` are allowed, so D26 is unchanged. SECURITY.md (WS5) records it.

| Principal | Allow | On |
| --- | --- | --- |
| API role | `dynamodb:GetItem`, `BatchGetItem`, `PutItem`, `UpdateItem`, `Query` | the table ARN |
| | `sqs:SendMessage` (covers `SendMessageBatch`) | the main queue ARN |
| | `ssm:GetParameter` | the token parameter ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | `<api log group ARN>:*` |
| | `kms:Decrypt` on the `aws/ssm` key, condition `kms:EncryptionContext:PARAMETER_ARN` = the parameter ARN | **Not needed** (G1, 2026-10-13: the first live submit read the SecureString with `WithDecryption=True` without this grant; the `aws/ssm` key policy authorizes account principals via SSM). Not granted |
| Worker role | `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes`, `ChangeMessageVisibility` | the main queue ARN |
| | `dynamodb:PutItem`, `UpdateItem`, `Query` | the table ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | `<worker log group ARN>:*` |
| Client role, grader user | `lambda:InvokeFunctionUrl` with `lambda:FunctionUrlAuthType = AWS_IAM`; `lambda:InvokeFunction` with `lambda:InvokedViaFunctionUrl = true` | the API function ARN |

No `cloudwatch:PutMetricData` (EMF instead), no X-Ray, no `logs:CreateLogGroup`. WS0's contract test (QMC-105) records every AWS operation each handler calls in the moto tests (a botocore `before-call` hook) and asserts it is in that function's allow-list above, so a missing permission fails in CI rather than as AccessDenied on Lambda.

### 8.4 Lambda environment variables

| Variable | API | Worker | Default |
| --- | --- | --- | --- |
| `QMC_ENV` | ✓ | ✓ | `dev` |
| `QMC_BUILD_SHA` | ✓ | ✓ | from `build/manifest.json` (`var.build_sha`, set by the Makefile) |
| `QMC_ENFORCE_PINNED_DISPATCH` | | ✓ | `true` |
| `TABLE_NAME` | ✓ | ✓ | `qmc-<env>` |
| `QUEUE_URL` | ✓ | ✓ | main queue URL |
| `METRICS_NAMESPACE` | ✓ | ✓ | `QMC/<env>` |
| `QRYPT_API_KEY_PARAM` | ✓ | | `/qmc/qrypt-api-key` |
| `QRYPT_URL` | ✓ | | `https://api-eus.qrypt.com/api/v1/quantum-entropy` |
| `QRYPT_DEADLINE_MS` | ✓ | | 2500 |
| `QRYPT_MIN_GAP_MS` | ✓ | | 2000 (minimum gap between Qrypt calls; contract change 006) |
| `SECRET_CACHE_TTL_S` | ✓ | | 300 |
| `MAX_PATHS`, `MAX_SHARDS`, `MAX_PATHS_PER_SHARD` | ✓ | | 10000000, 50, 1000000 |
| `JOB_DEADLINE_S` | ✓ | | 900 |
| `JOB_TTL_DAYS` | ✓ | | 365 |
| `WORKER_MEMORY_MB` | ✓ | | 1024 (cost estimate) |
| `MAX_RECEIVE_COUNT` | | ✓ | 3 (same Terraform variable as the redrive policy) |
| `RETRY_VISIBILITY_S` | | ✓ | 5 |
| `NPY_DISABLE_CPU_FEATURES` | | ✓ | pinned at G0 (§12) |
| `FAULT_INJECTION_ENABLED` | ✓ | ✓ | `false` (`var.enable_fault_injection`) |
| `LOG_LEVEL` | ✓ | ✓ | `INFO` |

The token parameter is fetched with `GetParameter(WithDecryption=True)` and cached at module level for `SECRET_CACHE_TTL_S`.

### 8.5 Packaging (`scripts/build_lambdas.py`, WS1)

- `build/api.zip`: `src/qmc_core/**` + `src/qmc_api/**` (no NumPy; `qmc_core/__init__.py` must not import `sim` or `fingerprint`).
- `build/worker.zip`: `src/qmc_core/**` + `src/qmc_worker/**` + NumPy installed with `pip install --platform manylinux_2_28_x86_64 --implementation cp --python-version 3.13 --only-binary=:all: --require-hashes -r requirements/worker.txt --target <tmp>`.
- Deterministic: files sorted, fixed timestamp (1980-01-01), fixed permissions, no `__pycache__` or `.pyc` (`pip install --no-compile`; `PYTHONDONTWRITEBYTECODE=1` alone does not stop pip's compile step). Building twice gives identical `sha256` (`build_lambdas.py --check` builds twice and compares), so `source_code_hash` never churns.
- Writes `build/manifest.json` (`git_sha`, `branch`, `dirty`, zip hashes). The manifest is **not** inside the zips (it would change their hash on every commit); the sha reaches Lambda as the `QMC_BUILD_SHA` environment variable.
- `make build` runs it; `make apply` depends on `make build`.

### 8.6 Variables (root)

`env` (`dev`), `region` (`us-east-2`), `alert_email` (required, no default), `qrypt_api_key_param_name` (`/qmc/qrypt-api-key`), `table_rcu`/`table_wcu` (10/10), `max_receive_count` (3), `worker_timeout_s` (10), `visibility_timeout_s` (60, validated ≥ 6 × worker timeout), `worker_memory_mb` (1024), `api_memory_mb` (512), `worker_max_concurrency` (5), `worker_reserved_concurrency` (**default `null`**; James's `dev.tfvars` sets 10 once the quota allows; the example file explains how to check with `aws lambda get-account-settings`), `build_sha` (set by the Makefile from the manifest), `log_retention_days` (14), `max_paths`/`max_shards`/`max_paths_per_shard`, `qrypt_min_gap_ms` (2000, validated 1000–10000), `job_deadline_s` (900), `enable_fault_injection` (false), `create_budget` (true), `budget_limit_usd` ("1.0"), `create_grader_user` (false), `api_zip`/`worker_zip` (`../build/*.zip`).

## 9. Observability

### 9.1 Custom metrics (EMF, namespace `QMC/<env>`, no dimensions)

One JSON log line per emission, written by `qmc_core.emf.emit(name, value, unit, **properties)` with `sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")` and a flush. **Never through Python `logging`:** on the Text log format the Lambda runtime's handler prefixes each record with level, timestamp and request id, and CloudWatch extracts EMF only from log events that are pure JSON objects. A unit test captures stdout and asserts every line parses as a JSON object:
`{"_aws": {"Timestamp": <ms>, "CloudWatchMetrics": [{"Namespace": "QMC/dev", "Dimensions": [[]], "Metrics": [{"Name": "JobsSubmitted", "Unit": "Count"}]}]}, "JobsSubmitted": 1, "job_id": "…"}`. Ids go in properties, **never** in dimensions.

| Metric | Unit | Emitted by |
| --- | --- | --- |
| `JobsSubmitted` | Count | API, per accepted job (replays included, with `replay_of` as a property) |
| `EntropyKiB` | Kilobytes | API, per Qrypt block consumed (1) |
| `PathsSimulated` | Count | Worker, per shard written (not on duplicates) |
| `ShardFailures` | Count | Worker, per shard marked failed |
| `JobsCompleted` | Count | Whoever finalizes COMPLETE |
| `JobsPartial` | Count | Whoever finalizes PARTIAL |
| `SeedingFailures` | Count | API, per job rejected for a Qrypt or secret failure (`cause` as a property) |
| `QryptLatencyMs` | Milliseconds | API, per Qrypt call |

Eight of ten free custom metrics (an empty dimension set is valid per the EMF specification). Verify at G1 with `aws logs filter-log-events --filter-pattern '{ $._aws.CloudWatchMetrics[0].Namespace = "QMC/dev" }'` and in the console (risk R8).

### 9.2 Alarms (all standard resolution, 60 s period unless noted, `treat_missing_data = "notBreaching"`)

| Alarm | Metric | Condition |
| --- | --- | --- |
| `qmc-<env>-dlq-depth` | `AWS/SQS ApproximateNumberOfMessagesVisible`, DLQ | Maximum > 0 |
| `qmc-<env>-worker-errors` | `AWS/Lambda Errors`, worker | Sum > 0 |
| `qmc-<env>-api-errors` | `AWS/Lambda Errors`, API | Sum > 0 |
| `qmc-<env>-worker-throttles` | `AWS/Lambda Throttles`, worker | Sum > 0 |
| `qmc-<env>-seeding-failures` | `QMC/<env> SeedingFailures` | Sum > 0 over 300 s |
| `qmc-<env>-jobs-partial` | `QMC/<env> JobsPartial` | Sum > 0 |
| `qmc-<env>-queue-age` | `AWS/SQS ApproximateAgeOfOldestMessage`, main queue | Maximum > 120 s |

Seven of ten free alarms. The Budget alert lives in Budgets, not CloudWatch.

### 9.3 Logs

One JSON object per line from `qmc_core.logjson`, written to stdout the same way as EMF (never through `logging` handlers): `ts`, `level`, `event`, `fn` (`api`/`worker`), `env`, `request_id`, and `job_id`, `shard_index`, `attempt` where known. Every Qrypt HTTP attempt (including the retry and failures) logs `event: "qrypt_call"` with `http_status` (null on a transport error), `latency_ms`, `attempt` (1 or 2) and `outcome` (`ok` or the §5.3 code); WS4's burst test counts these lines. A redaction filter drops any key named like `authorization`, `token`, `api_key`, `secret` or `block`, and the API never logs request headers. Retention 14 days. Lambda `log_format` stays `Text` (our lines are already JSON, and EMF extraction reads them).

## 10. Cost guardrails (I7)

| Rule | Why |
| --- | --- |
| Exactly **one** SQS event source mapping; never map the DLQ | An idle mapping long-polls ≈ 648k requests/month (with `maximum_concurrency` set it cannot scale down to 2 pollers); two mappings exceed the 1M free SQS requests |
| DynamoDB `PROVISIONED` 10/10, no autoscaling, no GSI, PITR off | Only provisioned capacity is free (25/25 per account per region, shared by every table and index) |
| ≤ 10 custom metrics and ≤ 10 alarms **across every environment** | Free tier counts per account; each dimension combination is a metric |
| No customer-managed KMS keys | $1/month each, the whole Budget |
| Log retention 14 days on every log group | "Never expire" grows storage forever |
| No Step Functions, NAT, ALB, EC2, RDS, API Gateway, Secrets Manager, S3, ECR, X-Ray | Not always-free, or not needed |
| `MAX_PATHS`, `MAX_PATHS_PER_SHARD`, worker reserved concurrency and `maximum_concurrency` | Cost runaway |
| Budget $1/month with an alert at the first cent | Backstop |
| Never emit a metric under two dimension sets | 8 + 8 would exceed the 10 free metrics |

The free-tier arithmetic for 100 jobs/day of 1M paths (PRIMER, "The free tier is won or lost…") is ≈ 7.5% of Lambda requests, ≈ 13% of GB-s, ≈ 72% of SQS requests (idle polling dominates), ≪ 1% of DynamoDB capacity, a few thousand KMS decrypts (secret cached 300 s; 20,000 free) and tens of MB of log ingestion (5 GB free): **$0.00**. WS5's `docs/COST.md` redoes it with the measured worker duration and idle-poll counts.

## 11. Verification (WS4) and the assignment's acceptance checks

| Assignment requirement | Proof (test or tool) | Gate |
| --- | --- | --- |
| Core 1: `POST /jobs` returns an id; record has seed hex, params, `QUEUED` | `tests/integration/test_submit.py` (moto); `tests/deployed/test_submit.py` | G1 |
| Core 2: distinct, reproducible shard seeds | `tests/core/test_seeds.py` (WS0) + `tests/correctness/test_seed_independence.py`: 1,000 indexes distinct; same master → same seeds; `spawn_key` ≡ `spawn()`; max cross-stream correlation of 20 streams × 10k normals < 5/√n | G1 |
| Core 3: progress then COMPLETE with estimate and CI | `make e2e` (`tools/e2e.py`) records at least one non-terminal observation (`RUNNING` or `progress.done < total`) before COMPLETE | G1 |
| Core 4: within 3 SE at 1M paths | `tests/correctness/test_closed_form.py` (frozen seed, committed) + `make e2e` on Lambda | G1 |
| Core 5: bit-identical replay | `tests/correctness/test_golden.py` in the Lambda image (CI) + `make e2e` + `make replay-check N=20` (`tools/replay_many.py`) | G1, G3 |
| Core 6: forced failure trips the DLQ alarm and notifies | `make warm-dlq` ≥ 15 min before, then `make force-failure` (`tools/force_failure.py`): job with `fail_shards=[3]` → PARTIAL with `[3]`, DLQ depth 1, alarm ALARM (poll up to 20 min), email received (screenshot) | G2 |
| Fail-closed seeding | Unit: every §5.3 Qrypt code via `FakeEntropySource` → right error, zero DynamoDB/SQS writes. Deployed: the integrator applies `EXTRA_VARS='-var qrypt_api_key_param_name=/qmc/qrypt-api-key-invalid'` (a decoy SecureString with a bad token, created by CLI) → WS4 asserts 503 `QRYPT_FORBIDDEN` (Qrypt answers a wrong token with 403, contract change 003), no job, `SeedingFailures` alarm → the integrator re-applies normally and deletes the decoy. Only 403 is demonstrated live; 401/429/500/503/timeout are unit-tested (TEST_RESULTS says so) | G2 |
| Duplicate SQS delivery | Integration: deliver each shard message twice and in a crash-between-writes order → same result, `n` unchanged. Deployed: re-send a shard message with `aws sqs send-message` after COMPLETE → job unchanged | G2 |
| Worker failure → PARTIAL | As core 6, plus reaper test with a shard message deleted by hand (moto) | G2 |
| Lambda timeout bound | Validation test for the ratio; worker duration at 1M paths/shard measured ≪ 10 s | G2 |
| Burst vs 30 req/10 s | `make burst` (`tools/burst.py`): 40 concurrent submits → Qrypt calls ≥ 2 s apart, ≤ 5 in any rolling 10 s (from the `RL#qrypt/LAST` history, `qrypt_call` lines and `QryptLatencyMs` samples); the rest 429 `ENTROPY_RATE_LIMITED` with `Retry-After`; no Qrypt 429 expected; any Qrypt 429 must fail closed as 503 `QRYPT_RATE_LIMITED` with nothing written | G2 |
| Cost runaway | Validation tests for `MAX_PATHS` and the ratio; reserved and maximum concurrency visible in `terraform show` | G2 |
| Invalid inputs | `tests/api/test_validation.py`: every §5.2 rule; deployed spot checks | G1 |
| Clean teardown, no drift | `make drift-check` (`tools/drift_check.sh`, run by the integrator from `main`): `plan -detailed-exitcode` = 0 → destroy → no tagged resources, no `/aws/lambda/qmc-*` log groups, no SNS subscriptions left → apply again (timed) → `make e2e --fresh` → plan = 0 → confirm the new SNS subscription | G2 |
| Empty / first run | `make e2e --fresh` right after an apply from empty: `GET /jobs` → `{"jobs": []}`; unknown id → 404 naming the id (without `--fresh`, e2e only checks that `jobs` is an array, so it can be rerun) | G1, G2 |
| Benchmarks | `make bench` (`tools/bench.py`): 50 warm submits paced ≥ 2.5 s start-to-start (above the 2 s gap with margin for latency jitter; any 429 counted and reported) for p50/p95; cold-start latency from natural cold starts (first request after ≥ 15 min idle, and REPORT lines with `Init Duration`), no configuration changes; 5 × 1M wall clock; apply timing → `docs/BENCHMARKS.md` | G3 |
| IAM completeness | WS0's operation allow-list test (QMC-105) over every moto test | G0+ |
| Coverage ≥ 80% | CI `pytest --cov --cov-fail-under=80`; `qmc_core` ≥ 90% | G3 |

## 12. Pinned versions (WS0, QMC-099)

| Item | Value |
| --- | --- |
| Python | 3.13 (Lambda `python3.13`) |
| NumPy | **2.5.4**, wheel `numpy-2.5.4-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`, sha256 `6ffa07666f8da0eef81d149934a626d0d95fbd6838432a33e66245423a9062c0` (`requirements/worker.txt`, same pin in `pyproject.toml`) |
| `NPY_DISABLE_CPU_FEATURES` | **`X86_V4 AVX512_ICL AVX512_SPR`**. NumPy 2.5 dispatches by feature *group*; in the Lambda image `np.show_runtime()` reports baseline `X86_V2`, dispatch targets `X86_V3`, `X86_V4`, `AVX512_ICL`, `AVX512_SPR`, so all three names are valid spellings. **Finding:** NumPy 2.5.4 silently ignores unknown names (`FOO`, `AVX512F` and `AVX2` all leave the target unchanged), so a typo would disable nothing; `tests/core/test_dispatch_lambda_image.py` asserts every listed name is a real dispatch group, and the fail-closed self-check remains the guard. The mechanism is proven by `NPY_DISABLE_CPU_FEATURES=X86_V3`, which drops `exp` to `baseline(X86_V2)` |
| `exp` float64 target under that setting, in `public.ecr.aws/lambda/python:3.13` | **`X86_V3`** (AVX2 + FMA). `exp` and `maximum` have `X86_V4` kernels (disabled); `add`, `multiply` and `subtract` have only `X86_V3`/baseline, so every float64 ufunc in §7.3 runs `X86_V3` on any AVX2 Lambda host (asserted by the golden job). Measured locally under Docker amd64 emulation on Apple silicon (Python 3.13.16, no AVX-512 visible); CI's `golden` job on an x86 runner is authoritative |
| Terraform | ≥ 1.10 (CI pins `hashicorp/terraform:1.16.5`) |
| AWS provider | `~> 6.0`; WS1 locked **6.68.0** in `infra/.terraform.lock.hcl` |
| tflint | `v0.64.0` (CI), AWS ruleset `0.49.0` (`infra/.tflint.hcl`, WS1) |
| Checkov | `3.3.28` (CI, `allow_failure` until QMC-119) |

## 13. Conventions

- **Versions:** Python 3.13 only; boto3 from the Lambda runtime for the API (not vendored); NumPy pinned. If a pinned version will not install, stop and report. Do not downgrade silently.
- **Thin handlers.** Handlers parse, call `qmc_core` and AWS, and map errors. All math, validation, seed derivation, aggregation, expression building and message encoding are pure functions in `qmc_core`, tested without AWS.
- **No NumPy in the API path.** `qmc_core.sim` and `qmc_core.fingerprint` are the only modules that import NumPy.
- **Floats:** never round, never store a Python float in DynamoDB, never compare results except by hex string.
- **Config:** every limit is an environment variable set by Terraform, with the defaults in §8.4, logged once at init.
- **No secrets** in the repo, tfvars examples, test fixtures or logs. `.env` and `*.tfvars` are gitignored.
- **Stay in your lane.** Edit only the paths your workstream owns (§4).
- **Tests are part of done.** A ticket is done when its tests pass from a clean checkout with `make test` (and `make test-golden` / `make e2e` where the ticket says so).
- **Git:** one branch per workstream; small commits prefixed `WSn:` with the ticket id; push to GitLab and open a merge request **into `staging`** (never `main`), with source-branch deletion unticked and `Closes #<issue>` / `Refs #<issue>`. GitLab CI runs only on merge requests. The integrator merges into `staging`, then promotes `staging` → `main` with one merge request (its pipeline must pass) at each gate or on James's request, pushes `main` to GitHub, and applies from `main` (D33, D42). Rebase your branch on `staging`.
- **AI usage log:** at the end of each session append to `docs/ai-log/WSn.md` (your own file): time, workstream, tool and model, what was asked, what was produced, what a human checked or changed. WS5 folds these into `AI_USAGE.md`.
- **Qrypt budget:** every live submit spends 1 KiB of a finite, unpublished monthly quota. Never loop live submits without a cap; poll at 2 s, not faster (10 RCU).
- **Verify, don't assert.** Every claim in a report or doc cites a test, a command output, a log line or a console screenshot.
- **Ask, do not guess,** when this file is silent on something that affects another workstream.
- **Record decisions** in `docs/DECISIONS.md` (via WS5): what we chose, the alternatives, why.

## 14. Known risks agents should respect

| # | Risk | Mitigation |
| --- | --- | --- |
| R1 | Qrypt self-registration closed or token delayed | Day 0 step 0.3; build against `FakeEntropySource`; email support |
| R2 | Qrypt monthly quota unknown; a 403 (`QRYPT_FORBIDDEN`, indistinguishable from a bad token) stops all submits until the reset | `size=1` always; the burst test uses 40 KiB at most; `SeedingFailures` alarm; keep total live usage far below 10 MB (≈ 200 KiB expected) |
| R3 | Qrypt latency vs the 800 ms p95 | Measure from Lambda at G1 (`QryptLatencyMs`); keep-alive connection; NumPy-free API zip; if p95 is over budget, document it with the numbers (a pre-fetched entropy pool is the fallback, not planned) |
| R4 | x86 Lambda hosts with AVX-512 change `exp` by 1 ULP and break replay | D2: pinned dispatch, fail-closed self-check, fingerprint, replay ×20 (QMC-146) |
| R5 | `NPY_DISABLE_CPU_FEATURES` names differ by NumPy version | WS0 checks them with `np.show_runtime()` in the Lambda image at G0 |
| R6 | New-account Lambda concurrency 10 or 50 | Day 0 check (request 1,000 only if under 110); `maximum_concurrency` 5; reserved concurrency nullable |
| R7 | SQS idle polling ≈ 648k requests/month | Exactly one mapping; check `NumberOfEmptyReceives` after 24 h |
| R8 | EMF lines not extracted (most likely cause: written through `logging`, which prefixes them) | Write to stdout only (§9.1) with a parse test; check with `filter-log-events` at G1. The empty dimension set itself is valid; if a fixed `Env` dimension is ever adopted, switch every emission (never both) |
| R9 | Function URL needs both invoke permissions (Oct 2025 change) | Identity policies grant both (§8.3); first signed call right after the first apply |
| R10 | `data "aws_ssm_parameter"` leaks the token into state | Banned (§8.1); `grep -r aws_ssm_parameter infra/` in CI must find no data source |
| R11 | Lambda auto-creates log groups and they survive destroy | Pre-created log groups, no `logs:CreateLogGroup`, `make drift-check` |
| R12 | Every destroy/apply creates a new, **unconfirmed** SNS email subscription (alarm emails are dropped until confirmed), and unconfirmed subscriptions cannot be deleted by Terraform | Confirm the email right after every apply from empty; "SNS subscription Confirmed" is a G2 and G4 check |
| R13 | Non-deterministic zips churn `source_code_hash` | Deterministic build + a test that builds twice |
| R14 | Time to DLQ is `maxReceiveCount × visibility` | Caught errors shorten visibility to 5 s, so a forced failure reaches the DLQ in ≈ 15–20 s; the alarm then depends on R23 |
| R15 | Free-plan accounts hide overage in credits | Paid plan (D25); the Budget alerts at the first cent |
| R16 | `GET /jobs/{id}` writes (lazy finalize) | Conditional on `status = QUEUED`; idempotent; tested |
| R17 | Graders cannot call an `AWS_IAM` URL without credentials | Grader user + keys sent privately (D26); README quick-start also lets them deploy their own |
| R18 | GitLab CI may have no runner on labs.gauntletai.com | Resolved: James's local runner (docker executor, `run_untagged`); GitHub Actions on each GitHub push as a second CI |
| R19 | Lambda "Web Functions" pricing line may classify Function URL traffic differently | Check Cost Explorer usage types after day 1; the Budget catches any spend |
| R20 | GitHub falls behind GitLab (pushes are manual) | The integrator pushes at every gate; sha comparison at every gate and before submission |
| R21 | An apply from the wrong checkout deploys stale code or default settings (grader user deleted, fault injection off) | D33: settings outside worktrees, apply from `main` after G1, guard in the Makefile, `build_sha` in `/health` |
| R22 | A grader's fresh account has a low concurrency quota | `worker_reserved_concurrency` defaults to null; README explains |
| R23 | SQS stops emitting metrics for a queue inactive for 6 h, and the first metrics after reactivation can take **up to 15 minutes**: the always-idle DLQ is the worst case | `make warm-dlq` (any SQS API call on the DLQ, e.g. `get-queue-attributes`) ≥ 15 min before a drill or recording, repeated every few hours while drilling; drill timeout 20 min; the video cuts the wait |
| R24 | Qrypt enforces a sliding limit of roughly one call per ~1.5 s (12:01Z: 8 calls in one second all 200; 12:55Z: 9 of 10 in one second got 429; 13:13Z: a call 1.19 s after the previous got 429; 13:20Z bench: 3 × 429 at a steady 1.5 s pacing) | 2 s minimum gap (D10, contract change 006), no retry on 429; any residual Qrypt 429 fails closed as 503 `QRYPT_RATE_LIMITED`. If 429s persist at 2 s, raise `qrypt_min_gap_ms` (Terraform only, no code change) |
