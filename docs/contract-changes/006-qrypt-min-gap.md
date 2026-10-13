# 006 Qrypt limiter: 2 s minimum gap, no retry on Qrypt 429

- **Requested by:** WS4 (bench finding), 2026-10-13 13:22 CT; pre-approved by James ("if it remains a problem … do gap of 2 s")
- **Status:** accepted; WS0 part on `main` (additive), WS1 and WS2 parts pending
- **Affects:** WS0 (`qmc_core.ddb.limiter_gap_take`, `limiter_gap_retry_after`, tests, HANDOFF 1.13 D10, §4.1, §6.1, §6.2, §7.5, §8.4, §8.6, §11, R24), WS1 (variable `qrypt_min_gap_ms` → env `QRYPT_MIN_GAP_MS`; remove `qrypt_window_s`, `qrypt_max_per_window`), WS2 (submit uses the gap builder; no retry on 429; tests), WS4 (burst expectations, bench pacing ≥ 2.5 s), WS5 (D10, README)

## Need

With 1 s windows (005), `make bench` at a steady 1.5 s pacing (13:20–13:22Z, 6dcb3c2) still got three Qrypt 429s (one 1.486 s and one 1.519 s after the previous call; the second's 285 ms retry also got 429). Qrypt's limit is sliding and stricter than one call per aligned second, and the retry after a 429 cannot succeed.

## Change

- New builder `ddb.limiter_gap_take(now, gap_ms)`: one item `RL#qrypt/LAST`, `SET at_ms = now` if `at_ms <= now - gap_ms` (race-free); `ddb.limiter_gap_retry_after(old_item, now, gap_ms)` for `Retry-After`.
- `QRYPT_MIN_GAP_MS` = 2000 replaces `QRYPT_WINDOW_S` and `QRYPT_MAX_PER_WINDOW`. ≤ 5 Qrypt calls per rolling 10 s.
- No retry on Qrypt 429; the one retry for 5xx/network stays, and only if the gap allows a new slot.
- If 429s persist at 2 s, raise `qrypt_min_gap_ms` in Terraform (no code change).

## Alternatives considered

- Keep 1 s windows (005) and document: steady use at 1.5 s pacing still fails some submits.
- A sliding-window counter: more state and expressions for no benefit over a single last-call timestamp at cap 1.

## Impact on invariants

None. I1 holds (fail closed, nothing written). The assignment's 30 per 10 s is met with a wide margin.
