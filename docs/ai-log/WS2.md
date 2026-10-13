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

## 2026-10-13, about 07:00 to 08:35 CT: contract changes 003 to 006 (MRs !9, !10, !12, !15, !18)

- **Workstream:** WS2 API (`ws2-api`)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`)
- **Asked (by WS0, from James's decisions):** implement each contract change in the API as WS0 froze it.
- **Produced:**
  - 003 (!9): Qrypt 403 maps to `QRYPT_FORBIDDEN`, fail fast, no retry.
  - 004 (!10): limiter cap default 10.
  - 005 (!15): one Qrypt call per 1 s window.
  - 006 (!18): replaced the window by a 2 s minimum gap between Qrypt calls (`limiter_gap_take`, Retry-After from the refused item's `at_ms`); a Qrypt 429 is never retried; the 5xx retry takes a fresh gap slot.
  - !12: fixed limiter tests that depended on the wall clock (fake clock in `test_submit.py`; `QRYPT_MIN_GAP_MS=0` in the conftest for clock-independent tests).
- **Verified:** `make test` green on each MR's pipeline; the parametrised gap tests (0, 0.5, 1.2, 1.999 s → 429 with Retry-After 2, 2, 1, 1; 2.0 s → 202).
- **Human checked or changed:** *(James: fill in at review)*

## 2026-10-13, about 11:00 to 14:40 CT: #52 coverage and the stretch items (MRs !33, !43, !44, !57)

- **Workstream:** WS2 API (`ws2-api`)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`)
- **Asked (by WS0):** #52 (`handler.py` to 100%); then QMC-128 PENDING_ENTROPY (contract change 008), QMC-127 Step Functions watcher (007), QMC-126 cost estimate in the job view, QMC-108 client `--antithetic` / `--queue`; then fix WS3's review findings and WS4's burst-drill result.
- **Produced:**
  - !33: `handler.py` at 100%; dropped a dead branch in `_json_default`.
  - !43: `qmc_api/pending.py`, the async self-invoked seeder (`{"qmc_seed", "attempt"}`, backoff from `PENDING_BACKOFF_MS`, conditional on `status = PENDING_ENTROPY`, never raises); submit's opt-in `on_entropy_failure: "queue"` with the `MAX_PENDING_ENTROPY` cap; GET's expiry backstop; `start_watch` (`ExecutionAlreadyExists` is success, other failures are logged and never change the response); `cost_estimate`, `entropy_attempts`, `last_entropy_error`, `pending_until`, `fail_reason` in the view.
  - !44, from WS3's review of !43: publish the `pending_to_queued` ALL_NEW item (no read between QUEUED and enqueue); re-invoke only when `entropy_attempts == attempt + 1`, else log `duplicate_seeder`; a JOBLIST failure while queueing fails the pending job. WS3 re-ran its probes and found nothing further.
  - !57, from WS4's live burst drill (5 of 10 seeded: seeders on one backoff schedule fought over the 2 s gap and each refusal spent an attempt): a refused slot is no longer an attempt. The seeder waits `retry_after` plus 0 to 2 s of jitter, in process when it fits, else re-invokes the same attempt with `not_before_ms` and `hop + 1`; hop 15 fails the job (`ENTROPY_RATE_LIMITED`) because Lambda's recursion detection drops about the 16th self-invoke in a Lambda-only chain (checked in the Lambda developer guide, "invocation-recursion"). Duplicate delivery was accepted as harmless: the seeder re-reads the job after every wait.
  - A discrete-event test clock (`Sim` in `tests/api/test_stretch.py`: a thread per seeder, one running at a time, `_sleep` advances a fake clock) for the burst test.
- **Verified:**
  - Burst of 10 in moto: 10/10 QUEUED, `entropy_attempts` all 1, max hop below 16. With `pending.py` reverted to the pre-!57 version, the same test fails with job 5 FAILED, as the live drill did.
  - WS4's `tests/integration/test_pending_entropy.py` passed unchanged on !57's branch; WS4's 3 new slot-wait tests fail on the old seeder.
  - `make test` on `main` 2fbd97e: 426 passed; `qmc_api` modules 94 to 100% covered.
  - Live (WS4, reported by WS0): G3 tagged `g3-proven` on `main` 2fbd97e, deployed code 8e0eac5; every stretch item verified live, the PENDING burst 10/10.
- **Not done:** abandoned PENDING jobs count against `MAX_PENDING_ENTROPY` until a GET expires them (accepted by WS0 as a documented limit).
- **Human checked or changed:** *(James: fill in at review)*
