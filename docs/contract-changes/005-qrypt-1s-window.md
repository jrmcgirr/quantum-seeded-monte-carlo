# 005 Qrypt limiter: 1 s window, cap 1 (`QRYPT_WINDOW_S`)

- **Requested by:** WS4 (burst finding), 2026-10-13 12:56 CT; accepted by James
- **Status:** accepted 2026-10-13; WS0 part on `ws0-foundation`, merged together with WS2 and WS1
- **Affects:** WS0 (`qmc_core.ddb`, `tests/core`, HANDOFF D10, §4.1, §7.5, §8.4, §8.6, §11, R24), WS1 (`qrypt_window_s` variable, `QRYPT_WINDOW_S` env, `qrypt_max_per_window` default 1), WS2 (`submit.py` reads `QRYPT_WINDOW_S`; `aws.py` defaults; tests), WS4 (burst expectations, bench pacing ≥ 1.5 s), WS5 (D10, README)

## Need

After contract change 004 (cap 10 per 10 s), the burst at 12:55:43Z let 10 calls through within about one second; Qrypt answered the first with 200 and the other 9 with **429** (`qrypt_call` lines 12:55:46.479–.587), so 9 submits failed closed as 503 `QRYPT_RATE_LIMITED`. Qrypt now enforces about 1 call/s (at 12:01Z it did not). A 10 s window cannot spread calls within the window.

## Change

- `ddb.LIMITER_WINDOW_S` = 1 (was 10). `ddb.limiter_take(window, cap, now, size_s=LIMITER_WINDOW_S)` (TTL uses `size_s`); `ddb.limiter_window(now, size_s)` unchanged in form.
- New env var `QRYPT_WINDOW_S` (API, default 1; Terraform `qrypt_window_s`, validated 1–10). `QRYPT_MAX_PER_WINDOW` default 1.
- The API passes `QRYPT_WINDOW_S` to both functions and to `Retry-After` (`max(1, ceil(window + size_s - now))`).
- Burst: about 1 accepted per second, the rest 429 `ENTROPY_RATE_LIMITED` with `Retry-After: 1`, no Qrypt 429. Bench pacing ≥ 1.5 s.

## Alternatives considered

- Keep 10 per 10 s and document: bursts become mostly Qrypt 503s and trip `SeedingFailures`.
- 1 per 10 s: far below Qrypt's limit; benchmarks take minutes.

## Impact on invariants

None. I1 holds (still fail closed). Any rolling 10 s sees ≤ 11 Qrypt calls (it can touch 11 aligned 1 s windows) (the assignment allows 30).
