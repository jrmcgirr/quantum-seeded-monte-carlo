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
