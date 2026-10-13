# 009 Shard billing fields for cost attribution

- **Requested by:** WS4 (drill 2 finding), 2026-10-13; decided by WS0
- **Status:** accepted; WS0 part in this MR; WS3 records the fields
- **Affects:** WS0 (`ddb.shard_put`, `cost.estimate`), WS3 (worker records them), WS4 (drill 2 assertion), WS5 (COST)

## Need

Drill 2 (job 166f446d, build 61d8b42): `cost_estimate.lambda_gb_seconds` was 0.473 but the job's REPORT lines sum to 2.381 GB-s. `duration_ms` times only the simulation (2 ms warm), while Lambda bills the whole invocation (18–60 ms warm) and, since 2025, the init phase of a cold start (365–436 ms + init).

## Change

- Shard item gains optional integers `invocation_ms` (handler entry → just before `shard_put`) and `init_ms` (module-import init time on the first record an execution environment processes, else 0). `ddb.shard_put(..., invocation_ms=None, init_ms=None)`; absent when not given.
- `cost.estimate` uses `invocation_ms + init_ms` per shard when present, else `duration_ms`.
- The API share stays a flat 0.125 GB-s per request (a warm-average estimate, documented).
- Drill 2 acceptance: worker GB-s within ±15% of the matched REPORT `Billed Duration` × memory (finalize work on the last shard after the put is not counted).
