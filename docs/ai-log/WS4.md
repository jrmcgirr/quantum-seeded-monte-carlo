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
