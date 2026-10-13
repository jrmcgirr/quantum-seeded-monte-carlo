# WS4 Verification: AI usage log

## 2026-10-13, morning (G0a → G0)

- **Tool and model:** Claude Code, Claude Opus 5.5 (session in the `ws4-verify` worktree), assigned by the WS0 session.
- **Asked:** execute `docs/prompts/WS4-verify.md`: read only until `g0a-interfaces`, then build QMC-140 to QMC-147 against the `qmc_core` interface stubs.
- **Produced:**
  - `tests/correctness/` (closed form, seed independence, golden; the golden constants are still empty and must come from the Lambda image).
  - `tests/integration/` (moto harness: submit, duplicate delivery, crash between `shard_put` and `mark_done`, out of order, forced failure → PARTIAL, reaper, replay, fail-closed 401).
  - `tests/deployed/test_edge_cases.py`.
  - `tools/`: `e2e.py`, `burst.py`, `warm_dlq.sh`, `force_failure.py` (with `--duplicate`), `replay_many.py`, `bench.py`, `drift_check.sh`, `_common.py`.
- **Interfaces negotiated with WS0/WS2:**
  - the `qrypt_call` log event, now contract in HANDOFF §9.3;
  - the `set_entropy_source` seam;
  - `FakeEntropySource(seed=…, failures=…)`;
  - `clients.qmc.request(method, path, body=None, url=None) -> (status, headers, json)`.
- **Checked:**
  - lint (ruff) is clean;
  - the seed-independence tests pass against a temporary reference `shard_generator` (max |corr| 0.028 < 0.05);
  - every other test fails only with the stubs' `NotImplementedError` or the missing WS2 module.
  - None of the tools has run against AWS yet.
- **Human check pending:** James reviews the tools before any live run, the fail-closed drill sequence and the Qrypt budget (e2e 1, burst ≤ 40, force-failure 1–2, bench ≤ 58 KiB).

## 2026-10-13, G0 → G3 (about 09:45–16:10 CT)

- **Tool and model:** Claude Code, Claude Opus 5.5, in the `ws4-verify` worktree. Coordinated with WS0, WS1, WS2 and WS5 by cross-session messages.
- **Asked:** run QMC-140 to QMC-148 on the deployed stack; record evidence in `docs/TEST_RESULTS.md` and `docs/BENCHMARKS.md`.
- **Produced:**
  - Golden constants, confirmed on emulated x86, native x86 (GitHub Actions) and real Lambda (`tools/shard_crosscheck.py`, 10/10 shards).
  - `make e2e` green.
  - Drills: DLQ, duplicate delivery, fail-closed.
  - Bursts under limiter versions 15 per 10 s, 10 per 10 s, 1 per 1 s and the 2 s gap.
  - Replay ×20, 20/20 bit-identical.
  - Log scan with a positive control (`tools/log_scan.py`).
  - Benchmarks, including an idle cold submit.
  - The drift-check evidence, and `drift_check.sh` per-service confirmation of tagging hits.
  - MRs !6, !13, !19, !22, !27, !32.
- **Findings raised (each one led to a decision recorded by WS0/James):**
  - Qrypt answers a bad token with 403 → contract change 003.
  - Qrypt rate-limits near-simultaneous calls → 004, then 005.
  - Qrypt rejects calls 1.19 s and 1.5 s apart → HANDOFF 1.12, then 006 (2 s gap).
  - The SCP blocks destroying the grader user → MR !28.
  - Coverage gaps in both handlers, including the worker's untested `algo_version` rejection.
- **Mistakes made and corrected:**
  - Burst's log poll stopped early and printed a false "no Qrypt 429" and "max 1" (WS5 caught the max). Fixed, and every affected line is listed in TEST_RESULTS.
  - Tool thread races (40 concurrent `terraform init`).
  - The bench timed URL resolution.
  - The SampleCount window was misaligned.
  - I first described the hosts' AVX-512 as "masked"; they report none.
  - Two unverified details in BENCHMARKS (client location, deploy count) and one prediction (p95 under 006) were removed.
  - I tested a bash function in zsh.
  - I told WS0 "pushed" once before pushing.
- **Human checks:** James accepted contract changes 003–006 and HANDOFF 1.12, supplied the DLQ alarm email screenshot, confirmed SNS subscriptions, and merged at the gates. The integrator (WS0) ran every apply and destroy, including the drift check.
- **Qrypt spent by WS4:** 89 KiB.

## 2026-10-13, stretch drills and the G3 record (about 16:20–20:30 UTC)

- **Workstream, tool, model:** WS4, Claude Code, Claude Opus 5.5. Tasked by WS0 (integrator) through cross-session messages; WS2 sent its seeder branch for verification.
- **Asked:**
  - verify the stretch tickets on the deployed stack: QMC-108 antithetic, QMC-126 cost, QMC-127 Step Functions watcher, QMC-128 PENDING_ENTROPY;
  - then the G3 record at the commit WS0 named (8e0eac5) and closing #41.
- **Produced:**
  - Tools: `tools/antithetic_drill.py` (incl. `--cost-only`), `tools/sfn_drill.py`, `tools/pending_drill.py`.
  - Tests: `tests/integration/test_sfn_definition.py`, `tests/deployed/test_sfn_definition.py`; 23 PENDING_ENTROPY moto tests, the last 3 for slot waits (verified to fail on the old seeder).
  - Updates: the 009-aware and then overhead-aware `test_cost.py`.
  - Docs: TEST_RESULTS sections for every drill, the final per-package coverage, and every §11 row at 8e0eac5 (re-run or carried, with the reason stated).
  - MRs !47, !52, !55, !58 and the G3 docs MR.
- **Findings raised (each led to a decision by WS0/James):**
  - The cost estimate missed billed work. It was 5× low before 009; after 009, cold starts passed and warm jobs came in at 0.170×. James chose a calibrated overhead (`WORKER_OVERHEAD_MS` 22), validated on fresh jobs at 0.887× and 1.015×.
  - The PENDING_ENTROPY backoff was synchronized: a 10-job burst gave 5 of 10 FAILED, one seed per retry wave. James chose "wait for slot + jitter" (WS2 !57); the re-run gave 10/10.
- **Mistakes made and corrected:**
  - My first slot-wait test wrote to DynamoDB inside the IAM guard; the guard caught it, and I fixed it with a clock skew.
  - The PENDING drill replayed the first queued job even when it had failed; it now picks a completed one.
  - The drill reported Qrypt spend as submits rather than calls.
- **Human checks:**
  - James chose both fixes and approved the extra Qrypt for the PENDING re-run.
  - WS0 ran every apply (watcher on and off, each followed by a plan showing no changes) and merged.
  - I verified the watcher removal myself, read-only.
- **Qrypt spent by WS4:** 113 KiB in total (24 KiB this session).
