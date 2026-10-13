# Benchmarks (WS4, QMC-147)

Measured with `make bench` (`tools/bench.py`) on the deployed `dev` stack in us-east-2, build `6dcb3c2`, 2026-10-13 13:20:31–13:22:21 UTC. Raw output is at the end. Evidence for every other requirement is in `docs/TEST_RESULTS.md`.

## Results against the assignment's targets

| Target (assignment) | Value | Measured | Result |
| --- | --- | --- | --- |
| Submit API latency, warm | < 800 ms p95 | client p50 **251 ms**, p95 **301 ms** (n = 49); Lambda `Duration` p50 105 ms, p95 151 ms | PASS |
| Job wall clock, 1,000,000 paths | < 60 s, 5 runs | server-side `completed_at − created_at`: **0.25, 0.46, 0.46, 0.18, 0.20 s**; client-observed ≤ 2.6 s | PASS |
| Steady-state cost | $0 at 100 jobs/day of 1M paths | arithmetic in `docs/COST.md` (WS5), using the durations below | see COST.md |
| Environment rebuild | `terraform apply` from empty < 10 min | from `make drift-check` (QMC-145, integrator) | **pending** |

## Environment

- **Client:** James's MacBook (Apple silicon), Python 3.13 via `uv`, `clients/qmc.py` (`botocore` SigV4) over the public internet to the us-east-2 Function URL. The client's network location was not measured, so client figures include an unmeasured WAN round trip.
- **API:** `qmc-dev-api`, python3.13, x86_64, 512 MB, Function URL `AWS_IAM`. The worker is 1,024 MB with event source mapping `maximum_concurrency` 5.
- **Qrypt limiter:** 1 call per 1 s window (contract change 005, HANDOFF 1.12).

## Method

### Submit latency (warm)
- 3 warm-ups, then **50** `POST /jobs` (1,000 paths, 1 shard), sequential, paced **1.5 s start-to-start** (≥ the limiter's 1 s window plus margin, HANDOFF 1.12).
- Each submit includes the Qrypt fetch (one 1 KiB block), the SSM-cached token, three DynamoDB writes and one SQS send.
- Latency is timed on the client (`time.monotonic()` around the signed request) and on the server, from the API's REPORT `Duration`, matched by `x-request-id`.
- Percentiles are **nearest-rank** (p95 is the 48th of 49 sorted values; no interpolation), so each figure appears in the raw list.
- **Non-202 submits are excluded from p50/p95 and reported separately:** 1 of 50 measured submits got 503 `QRYPT_RATE_LIMITED` (below). The 3 warm-ups are not measured; one of them also got a Qrypt 429.

### Wall clock, 1M paths
- 5 jobs of 1,000,000 paths / 10 shards (the reference case), one at a time.
- The client polls `GET /jobs/{id}` every 2 s (never faster: 10 RCU), so client-side times are an upper bound at 2 s resolution. The table below therefore also gives the **server-side** `completed_at − created_at` from the job view.
- All 5 were within 3 SE of Black-Scholes (z = −0.17, −0.73, 1.51, 1.24, 0.91).

| run | job | server-side s | client-observed s | z |
| --- | --- | --- | --- | --- |
| 1 | 380d4e44 | 0.252 | 2.6 | −0.17 |
| 2 | fdfbe252 | 0.463 | 2.6 | −0.73 |
| 3 | 881195c2 | 0.463 | 2.6 | 1.51 |
| 4 | 6db39906 | 0.177 | 0.4 | 1.24 |
| 5 | 24c89c6f | 0.196 | 0.5 | 0.91 |

Worker shard durations for 100,000 paths are 3–5 ms (shard items' `duration_ms`, e2e job edaff921). The 10 s worker timeout is about 2,000× that.

### Cold starts (natural only, no configuration change)
- Every REPORT line with `Init Duration` since the stack was created (2-day look-back).
- These cold starts are **not** idle-only: they also come from today's repeated deployments (every apply that changed a function's code or environment; not counted here) and from three 40-way bursts that scaled the API out.

| function | cold starts | Init Duration p50 | Init Duration max | Duration p50 (cold invocation) |
| --- | --- | --- | --- | --- |
| api | 137 | 375 ms | 520 ms | 677 ms |
| worker | 38 | 777 ms | 843 ms | 393 ms |

- A cold submit therefore costs about **375 + 677 ≈ 1.05 s** on the server (init, plus the first SSM read and a new TLS connection to Qrypt), above the 800 ms warm target. The assignment's target is for warm submits, and HANDOFF §3 reports cold separately.
- The worker's init includes the NumPy import and the SIMD self-check.
- **Pending:** one client-timed submit after ≥ 15 min idle (`make bench ARGS="--cold --submits 0 --runs 0"`, 1 KiB).

## Qrypt behaviour seen during the run (R24)

The limiter paced calls 1.5 s apart (median start-to-start 1.501 s over 59 calls), yet **Qrypt answered 429 three times**:

```
job 20ea6e94 (warm-up 3): 13:20:39.878Z 429 attempt 1 (1.519 s after the previous call's start), 13:20:40.186Z 429 attempt 2 (0.285 s later) → 503 QRYPT_RATE_LIMITED
job 734b76da (measured):  13:21:44.407Z 429 attempt 1 (1.486 s after the previous call's start) → 503 QRYPT_RATE_LIMITED
```

- Both failed closed: `seeding_failed`, no job written.
- So Qrypt's trial-key limit is stricter than 1 call per ~1.5 s, at least some of the time. This happened under **steady, paced load**, not only in a burst.
- The one retry (§7.5: 100–300 ms jitter, a new 1 s slot) is too soon to succeed against that limit.
- Reported to WS0 as the trigger that HANDOFF 1.12 names for the 2 s minimum-gap fallback (contract change 006).

## After contract change 006 (2 s minimum gap), build e77a626

A 3-submit steady spot check, paced 2.5 s: 3/3 accepted; client 286, 284, 298 ms; Lambda Duration p50 142 ms. Qrypt calls were 2.49–3.03 s apart and all got 200, with no 429. Details are in `docs/TEST_RESULTS.md`. Expect warm p95 to stay within the run above: the gap changes only how often a submit is admitted, not how long it takes.

## Qrypt spent
58 submits; 56 blocks delivered: **56 KiB**. WS4 total: 81 KiB (`docs/TEST_RESULTS.md`).

## Raw output

```
## Submit latency (warm), 50 submits after 3 warm-ups, paced 1500 ms

accepted (202): 49 of 50; **excluded from p50/p95:** {'503 QRYPT_RATE_LIMITED': 1} (a Qrypt 429 → 503 QRYPT_RATE_LIMITED is counted here, never in the latency sample)

| measure | n | p50 ms | p95 ms | max ms | target |
| --- | --- | --- | --- | --- | --- |
| client round trip (SigV4, laptop → Function URL) | 49 | 251 | 301 | 391 | p95 < 800 |
| Lambda REPORT Duration | 49 | 105 | 151 | 259 | p95 < 800 |

raw client ms: 295, 292, 286, 266, 304, 293, 295, 272, 391, 276, 237, 252, 260, 284, 251, 239, 243, 242, 249, 236, 242, 230, 233, 231, 240, 259, 237, 265, 235, 254, 263, 251, 248, 237, 251, 259, 262, 245, 237, 257, 240, 301, 238, 252, 240, 254, 244, 301, 250

## 1M paths, 10 shards: submit → COMPLETE, 5 runs

| run | job | seconds | status | target |
| --- | --- | --- | --- | --- |
| 1 | 380d4e44a70b47b491346b5467f57a21 | 2.6 | COMPLETE | < 60 |
| 2 | fdfbe252b91c499a8ece95e26bc26bac | 2.6 | COMPLETE | < 60 |
| 3 | 881195c23d264f4b8ad840ffacb74a3e | 2.6 | COMPLETE | < 60 |
| 4 | 6db3990633f94725a3673ea13de3ed57 | 0.4 | COMPLETE | < 60 |
| 5 | 24c89c6fbc8d4118b96a454066e4ebce | 0.5 | COMPLETE | < 60 |

median 2.6 s, max 2.6 s

## Natural cold starts (REPORT lines with Init Duration, last 2 days)

| function | cold starts | init p50 ms | init max ms | duration p50 ms |
| --- | --- | --- | --- | --- |
| api | 137 | 375 | 520 | 677 |
| worker | 38 | 777 | 843 | 393 |
```
