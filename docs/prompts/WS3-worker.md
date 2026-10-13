# WS3 — Worker

You are building the SQS-triggered worker Lambda: it simulates one shard, writes `{sum, sum_sq, n}` exactly once however many times SQS delivers the message, records the shard as done, and finalizes the job when it is the last one. When a shard keeps failing, the worker marks it failed on its last attempt so the job ends `PARTIAL` (never `COMPLETE` with a wrong total), and the message goes to the DLQ so the alarm fires. You gate G1 (**Tuesday 2026-10-13, 6:00 PM Central**) with WS1 and WS2.

**Start after:** G0a (tag `g0a-interfaces`, about 9:45 AM): code against the `qmc_core` interface stubs; your tests go green once G0 (noon) lands. Work on branch `ws3-worker` in `../quantum-seeded-monte-carlo-ws3`.

## Read first

1. `docs/HANDOFF.md`: §7.6 (worker) and §7.9 (fingerprint and SIMD self-check) are your specification; also §1 (I3, I4, I5), §6.2–6.3 (builders and the message), §7.3–7.4 (simulation and aggregation), §8.4 (environment), §9 (metrics, logs).
2. `docs/PRIMER.md`, section "One queue, one table, and conditional writes carry correctness".
3. `CLAUDE.md`, and `qmc_core`: `messages`, `sim`, `fingerprint`, `ddb`, `aggregate`, `emf`, `logjson`.
4. Your tickets in `docs/ROADMAP.md`: QMC-130 to QMC-132.

## You own

`src/qmc_worker/**`, `tests/worker/**`, `docs/ai-log/WS3.md`. Every rule comes from `qmc_core`; the handler is thin. Use WS0's IAM allow-list fixture in every moto test: the worker may call only the operations in HANDOFF §8.3 (no `GetItem`).

## Build in this order

1. **Init.** At module import: `qmc_core.fingerprint.assert_reproducible_dispatch()` (raises if NumPy dispatched `exp` to an AVX-512 target **and** `QMC_ENFORCE_PINNED_DISPATCH=true`, which Terraform sets on Lambda; elsewhere it warns, so your tests import the handler on any CPU), compute the fingerprint, create the boto3 resources once, log the configuration once.
2. **Happy path (QMC-130).** Batch size 1, but loop over `Records` anyway and return `{"batchItemFailures": [...]}`. Decode → simulate (timed) → `shard_put` (a conditional failure is a duplicate: log it and continue) → `mark_done` (a conditional failure means the job item is gone: log `orphan_shard`, treat the record as processed) → if all shards are accounted for and the job is `QUEUED`, `shards_query` + `aggregate` + `finalize_complete`/`finalize_partial` (a conditional failure means someone else finalized) → EMF.
3. **Failure path (QMC-131).** On an exception, first `ChangeMessageVisibility` to `RETRY_VISIBILITY_S` (on every attempt, including the last, because SQS moves the message to the DLQ on the next receive). Then, on the last attempt (`ApproximateReceiveCount ≥ MAX_RECEIVE_COUNT`), `mark_failed` (refused if the shard is already done; use `ReturnValuesOnConditionCheckFailure=ALL_OLD` so the refusal hands you the item), finalize PARTIAL if everything is accounted for and emit `ShardFailures`. Always report the item failure. `ForcedFailure` when `msg.fail` and `FAULT_INJECTION_ENABLED`.
4. **At-least-once tests (QMC-132).** Duplicates (sequential and concurrent), a crash between `shard_put` and `mark_done` followed by redelivery, two workers finishing the last two shards at once, a poison message, the last-attempt path.

## Requirements that are easy to get wrong

- **A duplicate is not a reason to stop.** When `shard_put` fails its condition, an earlier attempt may have crashed before `mark_done`. Always run `mark_done` and the finalize check.
- **Never overwrite a shard item** and never recompute it into a different value: the first write wins, and with pinned dispatch the recomputation would be identical anyway.
- Finalize reads shards with `ConsistentRead=True` and aggregates in index order via `qmc_core.aggregate`. Partial results use only shards in `done_shards`.
- Missing `done_shards`/`failed_shards` attributes mean empty sets.
- `ApproximateReceiveCount` is a string attribute in the SQS record; compare as an integer.
- Do not consume, read or purge the DLQ from code.
- `PathsSimulated` is emitted only when this invocation's `shard_put` succeeded, so duplicates never inflate the metric.
- Stay under the 10 s timeout: at the 1,000,000-paths-per-shard maximum the simulation is well under a second; cold start (NumPy import) is the main cost. Log `duration_ms` and `init` once.
- Store `duration_ms`, `memory_mb` (from `context.memory_limit_in_mb`), `attempt` and the fingerprint (with `cpu_model`) on the shard item.

## Done when

- `uv run pytest tests/worker` is green against moto.
- On the deployed stack: a 1M-path job reaches COMPLETE; the worker logs show one `shard_written` per shard and the fingerprint; a forced failure (`fault_injection.fail_shards=[3]`, deployment flag on) ends PARTIAL with `[3]` and one message in the DLQ.
- You report: cold and warm durations from the REPORT lines, the `exp_target` and `cpu_model` values seen, and an AI usage log entry in `docs/ai-log/WS3.md`.

## Review checklist for James

- [ ] The duplicate branch continues to `mark_done` and finalize
- [ ] `mark_failed` is only called on the last attempt and is refused for a done shard
- [ ] Concurrency test: two workers racing the final shards produce exactly one finalize write
- [ ] No code path reads the DLQ
- [ ] The init self-check raises before any message is processed when dispatch is wrong
