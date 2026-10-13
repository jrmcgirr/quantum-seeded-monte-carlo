# 004 Qrypt limiter cap 10 per 10 s

- **Requested by:** James, 2026-10-13 CT (the Qrypt trial key is rated 1 call/s)
- **Status:** accepted 2026-10-13 by James
- **Affects:** WS0 (HANDOFF D10, §7.5, §8.4, §8.6, §11, R24; ROADMAP; prompts), WS1 (`infra/variables.tf` default and description), WS2 (`src/qmc_api/aws.py` env default), WS4 (`tools/bench.py` pacing, `tools/burst.py` assertion, burst re-run), WS5 (DECISIONS D10, README guardrails)

## Need

The Qrypt trial key is rated 1 call/s. The limiter allowed 15 per fixed 10 s window (≤ 30 in any rolling 10 s, the assignment's figure). Today's logs show Qrypt did not enforce 1/s (26 calls with 200, 0 with 429, up to 8 in one second during the burst), but sustained load above 1/s risks 429s during grading.

## Change

- `QRYPT_MAX_PER_WINDOW` default 15 → **10** (Terraform `qrypt_max_per_window` default 10, validation stays 1–15). Window stays 10 s; the limiter code is unchanged.
- Any rolling 10 s now sees ≤ 20 Qrypt calls; the average is ≤ 1/s.
- Burst expectation: 40 concurrent submits → 10 × 202 and 30 × 429 `ENTROPY_RATE_LIMITED` per window; ≤ 20 per rolling 10 s.
- Bench pacing ≥ 1.0 s start-to-start.

## Alternatives considered

- Keep 15 and document: no work, but sustained use could exceed the trial key's rate.
- A strict 1 s window with cap 1: needs a configurable window (a `qmc_core.ddb` change) and makes the burst and benchmarks slower; the per-second burst risk is already covered by fail-closed `QRYPT_RATE_LIMITED`.

## Impact on invariants

None. I1 holds (a Qrypt 429 still fails closed); I7 unchanged.
