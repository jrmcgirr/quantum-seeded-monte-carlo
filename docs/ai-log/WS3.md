# WS3 Worker: AI usage log

## 2026-10-13, morning (G0a → G0)

- **Workstream:** WS3 Worker (QMC-130, QMC-131, QMC-132)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`), driven by a WS0 coordinator session
- **Asked:** run `docs/prompts/WS3-worker.md`: read HANDOFF, plan until `g0a-interfaces`, then build the worker against the `qmc_core` stubs and make `tests/worker` green at `g0-contract-frozen`.
- **Produced:**
  - `src/qmc_worker/handler.py`: init self-check and fingerprint, conditional shard put (duplicates go on to `mark_done` and finalize), orphan handling, finalize via `aggregate.decide` with one re-decide from `ALL_OLD`, failure path (5 s visibility on every attempt, `mark_failed` on the last, PARTIAL finalize, `ShardFailures` only on a successful mark), poison messages.
  - `tests/worker/`: 13 moto tests under `iam_guard("worker")`. They cover random-order completion matching a direct simulation's hex, the init self-check, orphan shards, a forced failure (3 attempts, PARTIAL `[3]`, DLQ), a done shard never marked failed, poison messages, sequential and interleaved duplicates, a crash between writes, and finalize races. The races use deterministic interleavings, not threads.
  - Asked WS0 six interface questions before coding. The answers are now in HANDOFF §4.1.
- **Verified:**
  - On `g0-contract-frozen` (8222b9b): `uv run pytest tests/worker`: 13 passed. Full suite: 221 passed. Handler coverage: 88%.
  - The first run had 3 failures, all bugs in my tests (a recursive hook, and test reads inside `iam_guard`).
  - Mutation check: a duplicate that returns early fails 2 tests, and `mark_failed` on every attempt fails 2 tests.
- **Human check:** pending. James to review the MR against the checklist in `docs/prompts/WS3-worker.md`. Deployed checks (1M-path COMPLETE, forced failure → PARTIAL + DLQ, REPORT durations, `exp_target`/`cpu_model`) wait for the G1 deploy.

## 2026-10-13, late morning (deployed checks on build c768aea, us-east-2)

- **Workstream:** WS3 Worker. **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`).
- **Asked:** run the WS3 deployed checks once WS1 had applied and the token parameter existed. Two live submits, 2 KiB of Qrypt.
- **Produced and verified** (`clients/qmc.py` with the `qmc` profile; worker logs via `FilterLogEvents`):
  - **1M-path reference job** `ce48c4378f45410297ab3484776b3a9d`: COMPLETE.
    - `estimate_hex 0x1.4dff91f39582cp+3` (10.437447524786045), SE 0.014714, z −0.893, `within_3se: true`.
    - Qrypt latency 118 ms.
    - Exactly one `shard_written` per shard (10, all attempt 1) and one `job_finalized`.
  - **Forced failure** `b33493db9834470986ffc5264528fdca` (`--fail-shards 3`, fault injection on): PARTIAL in about 11 s.
    - `failed_shards [3]`, progress 9/10 done and 1 failed, `partial_result.n = 900000`.
    - Worker trail: `shard_error` on attempts 1, 2 and 3 at 11:27:03, :08 and :13 (5 s visibility), then `shard_marked_failed`, `ShardFailures`, `job_finalized PARTIAL [3]`, `JobsPartial`.
    - DLQ `ApproximateNumberOfMessages = 1` after about 90 s (0 at about 35 s: the count lags). The main queue is empty.
  - **Runtime:** `exp_target X86_V3`, `dispatch_enforced true`, `avx512f false`, `fma true`, NumPy 2.5.4, Python 3.13.15, glibc 2.34.
    - `cpu_model` values seen: `Intel(R) Xeon(R) Processor @ 2.50GHz`, and `Intel(R) Xeon(R) Processor` on one sandbox.
  - **REPORT durations:**
    - Cold (5 sandboxes): Init 605–837 ms, Duration 373–397 ms.
    - Warm: Duration 17–49 ms (typically about 20 ms).
    - Max memory 119–121 MB of 1024.
    - Simulation of 100k paths: 3–8 ms warm.
- **Human check:** pending (James).

## 2026-10-13, afternoon (edge tests, stretch tests, seeder review, contract change 009)

- **Workstream:** WS3 Worker. **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`), directed by WS0, WS2 and WS4 sessions over cross-session messages.
- **Asked and produced:**
  - **Edge-path tests** (#51, MR !34): `tests/worker/test_edges.py`. Covers refusing a message with another `algo_version` (never simulated, I5), DynamoDB throttles on `shard_put` / `mark_done` / `finalize_complete` being reported and then repaired by redelivery, `mark_failed` itself failing, the job vanishing before finalize or on the last attempt, and paginated shard queries. No handler change was needed.
  - **Stretch tests** (#48, #54, MR !39): `tests/worker/test_stretch.py`. Antithetic shards store pairs (`n` = paths / 2, and the result matches a direct simulation). A PENDING_ENTROPY job has no seed and no deadline until it is seeded, then completes. A stray message never finalizes a pending job. `conftest.make_job` gained `variance_reduction`.
  - **Review of WS2's PENDING_ENTROPY seeder** (MR !43): found two confirmed bugs, duplicate delivery and a job left QUEUED with no messages, plus two minor gaps. Re-reviewed the fixes in MR !44 (280b173) with throwaway probes and confirmed them; nothing from the probes was committed.
  - **Contract change 009** (#49, MR !48, 9b0d7ab): shard items record `invocation_ms`, measured from `_process` entry to just before `shard_put`, and `init_ms`.
    - `INIT_MS` is measured at module init and charged only to the first shard each sandbox writes; later shards get 0. WS0 accepted this deviation from the draft.
    - Test: `test_billing_fields_charge_init_once_and_time_the_invocation`.
- **Verified:**
  - `uv run pytest tests/worker` was green on each branch.
  - Handler coverage reached 99% after !34. The only gap is the `197→exit` branch.
  - The full suite on `ws3-billing`, rebased on main after WS4's !47, gave 416 passed.
  - All four MRs passed CI and were merged by the integrator.
  - Deployed result after 009 (WS4, `docs/TEST_RESULTS.md`, build 2b7bd48): cold invocations passed at 0.856×; warm ones failed at 0.170×. The cause is billed work after `shard_put` (the put, `mark_done`, EMF, finalize), about 15–45 ms per invocation, which `invocation_ms` does not cover by design.
  - Resolution, chosen by James: WS0 added `cost.WORKER_OVERHEAD_MS = 22`. WS4 then measured 0.887× on a mixed run and 1.015× all-warm, both PASS (581563d). The worker was not changed.
- **Human check:** James chose the cost resolution, option (b) `WORKER_OVERHEAD_MS`, and the integrator merged the MRs. The code review of the MRs was done by peer sessions; no line-by-line human review is recorded.
