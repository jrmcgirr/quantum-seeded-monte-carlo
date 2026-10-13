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

## 2026-10-13, 11:26 CT: G1 live check (build c768aea, us-east-2): one Qrypt block spent

```
uv run python clients/qmc.py submit --S0 100 --K 100 --r 0.05 --sigma 0.2 --T 1 --paths 1000000 --shards 10
  → 202 job 5358ec35af8a450d9eba939da9f610d9, master_seed_hex 36ffd99c…a530
uv run python clients/qmc.py wait 5358ec35af8a450d9eba939da9f610d9
  → COMPLETE 10/10. estimate 10.476167161519855 (0x1.4f3cc2ea45059p+3), SE 0.014748685456949651,
    ci95 [10.447260269204925, 10.505074053834786]; Black-Scholes 10.450583572185565, z 1.735, within_3se true
    seed_provenance: source qrypt, endpoint api-eus, http 200, latency 310 ms, raw_sha256 e4335935…a3b8
    fingerprint: python 3.13.15, numpy 2.5.4, glibc 2.34, exp_target X86_V3, dispatch_enforced true, avx512f false
uv run python clients/qmc.py replay 5358ec35af8a450d9eba939da9f610d9   → 202 job f1df7275ea874eb5a3c6db396353c2da
uv run python clients/qmc.py wait f1df7275ea874eb5a3c6db396353c2da
  → COMPLETE; seed_provenance {"source":"replay","replay_of":"5358ec35…"}
    replay_check: bit_identical true, estimate_equal true, shards_compared 10, shard_diffs [], fingerprint_match true
```

- **kms:Decrypt (HANDOFF §8.3):** not needed. `GetParameter(WithDecryption=True)` on the `aws/ssm`-encrypted SecureString succeeded with the API role's `ssm:GetParameter` alone (the job seeded and returned 202).
- **Latency from CloudWatch** (2 live submits, so no p95 yet; WS4's `make bench` gives p50/p95):

  | Submit | REPORT duration | QryptLatencyMs | Notes |
  | --- | --- | --- | --- |
  | Mine, 11:26:10 | 488.30 ms | 310 | Warm container (no `Init Duration`), but the first Qrypt TLS connection and the first SSM fetch |
  | Not mine (likely WS4's e2e), 11:26:27 | 179.16 ms | 144 | Keep-alive reused, secret cached |

  Earlier cold inits were 83.84 ms and 295.72 ms.
- **Worker REPORT:** 17–45 ms per 100k-path shard, 121 MB of 1,024 MB.
- **Observation for e2e:** a 1M/10 job completes in about 1 s, so `wait` started from a separate process saw COMPLETE on its first poll. Observing a non-terminal state needs a poll immediately after the 202.
