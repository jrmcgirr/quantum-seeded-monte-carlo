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

## 2026-10-13, about 11:15 CT: live checks against the deployed API (build c768aea, us-east-2)

Evidence handed to WS4 for `docs/TEST_RESULTS.md`. Signed with the `qmc` profile:

```
export AWS_PROFILE=qmc QMC_API_URL=https://mk3mmjsv7lvhkh5isgfmcm4fem0ufzrw.lambda-url.us-east-2.on.aws
uv run python clients/qmc.py health   # 503 {"status":"error","checks":{"dynamodb":"ok","ssm":"error: ParameterNotFound"},"build_sha":"c768aea3023263d046fadfb3e3fa9f64f5ddd2ae","env":"dev"}
uv run python clients/qmc.py list     # 200 {"jobs": []}
uv run python clients/qmc.py get 00000000000000000000000000000000   # 404 JOB_NOT_FOUND "No job with id '000…0'."
uv run python clients/qmc.py get nope                               # 404 JOB_NOT_FOUND "No job with id 'nope'."
```

The same runs through `clients.qmc.request` (every response had `x-request-id`):

```
POST /jobs {reference, 1M paths, 10 shards}      503 SECRET_UNAVAILABLE   (fail closed, I1)
GET  /jobs                                       200 {"jobs": []}          (nothing written)
POST /jobs {S0: -1, x: 1}                        400 VALIDATION_ERROR, 2 details
DELETE /jobs                                     405 METHOD_NOT_ALLOWED
GET  /nope                                       404 ROUTE_NOT_FOUND
POST /jobs {+ fault_injection [3]}               503 SECRET_UNAVAILABLE   (fault injection enabled in dev)
curl unsigned /health                            403
```

CloudWatch Logs `FilterLogEvents` on `/aws/lambda/qmc-dev-api`, last hour:
- `{ $.SeedingFailures = 1 }`: 2 EMF lines, namespace `QMC/dev`, `Dimensions: [[]]`, `cause: SECRET_UNAVAILABLE`
- `{ $.event = "seeding_failed" }`: 2
- `"Bearer"`: 0. `"authorization"`: 0.
- REPORT: `Init Duration` 83.84 ms and 295.72 ms; a cold submit took 842.08 ms (it failed at SSM, so no Qrypt call).

Blocked: `/qmc/qrypt-api-key` does not exist in us-east-2 (HANDOFF Day 0 step 0.4, James).
