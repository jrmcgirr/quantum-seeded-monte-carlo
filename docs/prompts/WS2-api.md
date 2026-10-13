# WS2 — API

You are building the API router Lambda behind the Function URL: job submission (rate limit, Qrypt entropy, master seed, job record, fan-out), status with lazy finalize and the deadline reaper, replay with a bit-level comparison, health, and the SigV4 CLI client graders will use. Submission is where fail-closed seeding lives: if Qrypt or the secret fails, the job is rejected with an error naming the cause and **nothing** is written. You gate G1 (**Tuesday 2026-10-13, 6:00 PM Central**) together with WS1 and WS3.

**Start after:** G0a (tag `g0a-interfaces`, about 9:45 AM): code against the `qmc_core` interface stubs; your tests go green once G0 (`g0-contract-frozen`, noon) lands. Work on branch `ws2-api` in `../quantum-seeded-monte-carlo-ws2`.

## Read first

1. `docs/HANDOFF.md`: §5 (API contract) and §7.5, §7.7, §7.8 (submit, lazy finalize and reaper, replay) are your specification; also §1 (I1, I3, I4, I6), §6 (data model and the builders you must use), §8.4 (environment), §9 (metrics and logs).
2. `CLAUDE.md`, and `qmc_core`: `params`, `seeds`, `ddb`, `messages`, `errors`, `aggregate`, `replay`, `bs`, `emf`, `logjson`.
3. Your tickets in `docs/ROADMAP.md`: QMC-120 to QMC-126.

## You own

`src/qmc_api/**`, `tests/api/**`, `clients/**`, `docs/ai-log/WS2.md`. Use `qmc_core` for every rule; do not reimplement validation, seed derivation, expressions or aggregation.

## Build in this order

1. **Router and read paths (QMC-120).** Function URL payload 2.0: method from `requestContext.http.method`, path from `rawPath`, base64 bodies. A small route table; JSON responses with `x-request-id`; every exception mapped to a §5.3 response. `GET /jobs` from the `JOBLIST` partition (newest first, limit 50) plus one `BatchGetItem` for the current statuses; `GET /jobs/{id}` with a consistent read (an id that is not 32 lowercase hex is a 404); `/health` (DynamoDB `GetItem` on `HEALTH/HEALTH`, SSM `GetParameter` without decryption, plus `build_sha` and `env`). Use WS0's IAM allow-list fixture in every moto test.
2. **Secret and entropy (QMC-121).** SSM fetch with a module-level TTL cache. `EntropySource` interface with `QryptHttpSource` (stdlib `http.client`, one connection reused across warm invocations, overall 2,500 ms deadline bounded by the remaining Lambda time, at most one jittered retry for 429/5xx/network, never for 400/401/403; a keep-alive connection closed while idle is reopened once immediately and does not count as the retry) and `FakeEntropySource` (SHAKE-256 blocks, injectable failures for every code). This fake is the assignment's "mock Qrypt client".
3. **Submit (QMC-122).** Exactly HANDOFF §7.5: validate → limiter slot → secret → Qrypt → entropy claim → master seed → job put → JOBLIST put → `SendMessageBatch` (10 per call, retry failed entries twice, else `mark_enqueue_failed` and 503; any other failure after `job_put` also ends in `mark_enqueue_failed` and 503) → EMF → 202.
4. **CLI client (QMC-125).** `clients/qmc.py`: SigV4 with botocore (service `lambda`), commands `submit`, `get`, `wait` (poll every 2 s, print each status change), `replay`, `list`, `health`. WS4 and the graders depend on it, so ship it as soon as `/health` works.
5. **Lazy finalize and reaper (QMC-123).** On `GET` of a `QUEUED` job (read consistently), call `qmc_core.aggregate.decide()`; finalize or reap with the conditional builders. A PARTIAL write carries the done-count snapshot; if its condition fails, re-read and re-decide once. Test the interleaving where a worker completes the last shard between your read and your PARTIAL write: the job must end COMPLETE. Add the Black-Scholes `reference` block.
6. **Replay (QMC-124).** HANDOFF §7.8; `replay_check` via `qmc_core.replay.compare` once both jobs are terminal.
7. **Cost attribution (QMC-126, stretch).**

## Requirements that are easy to get wrong

- **Fail closed, write nothing.** On any secret or Qrypt failure: no job item, no JOBLIST item, no entropy item, no SQS message. Only the limiter slot is consumed. Emit `SeedingFailures` with the cause.
- **Order matters:** the limiter slot comes before the Qrypt call, and a retry takes a new slot. The cap is 15 per 10 s window, not 30 (two adjacent windows can both fill).
- Never log the token, the `Authorization` header, request headers in general, or the raw entropy block. Log `raw_sha256` only.
- Qrypt error mapping is exact (HANDOFF §5.3). A 403 is `QRYPT_FORBIDDEN` (bad token **or** quota used up: Qrypt sends the same response for both, contract change 003), not a generic failure: operators need to know the monthly allotment is gone.
- The overall Qrypt deadline is one budget for connect + TLS + request + read + any retry. `urllib`/`http.client` timeouts are per socket operation, so compute each from the time left.
- Lambda `GET` writes (lazy finalize, reaper) are conditional on `status = QUEUED`; a second `GET` must write nothing.
- `GET /jobs` must never Scan. A fresh deployment returns `{"jobs": []}` with 200.
- Replay never calls Qrypt, never takes a limiter slot, never copies `fault_injection`.
- Floats in responses: emit the exact double (Python's `json` uses `repr`, which round-trips) and the hex string.
- `fault_injection` is rejected unless `FAULT_INJECTION_ENABLED=true`.
- Handlers never raise; Lambda errors trip the `api-errors` alarm.

## Done when

- `uv run pytest tests/api` is green, with moto and `FakeEntropySource`, no network, no token.
- Against the deployed stack (with WS1 and WS3): `python clients/qmc.py submit --S0 100 --K 100 --r 0.05 --sigma 0.2 --T 1 --paths 1000000 --shards 10` then `wait` reaches COMPLETE with `reference.within_3se: true`; `replay` then `wait` shows `replay_check.bit_identical: true`.
- You report: warm and cold submit latency from the REPORT lines, Qrypt latency from `QryptLatencyMs`, and an AI usage log entry in `docs/ai-log/WS2.md`.

## Review checklist for James

- [ ] A test proves every Qrypt failure writes zero items and sends zero messages
- [ ] A log-capture test finds no token or Authorization header
- [ ] Retry logic never retries 401/403 and takes a second limiter slot
- [ ] `GET /jobs` uses Query on `JOBLIST`, not Scan
- [ ] Replay's provenance says `"source": "replay"` and the Fake source's call count is unchanged
- [ ] Every handler path returns a §5.3 body on exceptions
