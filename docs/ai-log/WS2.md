# WS2 API: AI usage log

## 2026-10-13, 05:40 to about 11:00 CT: G0a → G0

- **Workstream:** WS2 API (`ws2-api`)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`)
- **Asked:** execute `docs/prompts/WS2-api.md`: read and plan until `g0a-interfaces`, then code QMC-120 to 125 against the `qmc_core` stubs and go green at `g0-contract-frozen`.
- **Produced:**
  - `src/qmc_api/` (`handler`, `jobs`, `submit`, `qrypt`, `secret`, `aws`).
  - `clients/qmc.py`.
  - `tests/api/`: 79 tests, 91% coverage over `qmc_api` + `clients`.
  - The two halves of the fake Qrypt source share one retry policy (in `EntropySource.fetch`), so the fake exercises the same deadline, retry and slot logic as the HTTP client.
- **Contract traffic with WS0:**
  - Three questions answered and recorded in HANDOFF §4.1: a refused second slot returns the Qrypt error; `QryptLatencyMs` is emitted per attempt; the fake lives in `qmc_api.qrypt`.
  - One gap found: `ALGO_VERSION` and `CHUNK_SIZE` were only reachable through NumPy-importing `sim`; WS0 moved them to `params`.
  - Two WS4 requests implemented: an importable `request()` and the fake's constructor.
- **Verified:**
  - `uv run pytest tests/api`: 79 passed.
  - WS4's `tests/integration` with WS3's worker: 13 passed.
  - `make test`: one failure, WS0's G0a stub test `test_stub_handlers_return_200`, now obsolete and reported to WS0.
- **Not yet verified:** anything against the deployed stack (latency, `make e2e`); that waits for WS1's apply.
- **Human checked or changed:** *(James: fill in at review)*
