# 008 Opt-in PENDING_ENTROPY queued retry (QMC-128, #54)

- **Requested by:** James (stretch scoping), 2026-10-13
- **Status:** accepted 2026-10-13
- **Affects:** WS0 (`qmc_core.params`, `qmc_core.ddb`, `qmc_core.aggregate.decide`, HANDOFF §1 I1, §5, §6, §7.5, §7.7, §8.4), WS1 (IAM self-invoke, event-invoke config, env), WS2 (routing, seeder, view, client), WS4 (tests, drill), WS5 (docs)

## Request

`on_entropy_failure`: optional, `"reject"` (default) or `"queue"`; anything else is a VALIDATION_ERROR detail. `JobSpec.on_entropy_failure` carries it.

## Which failures queue

Queue (only with `"queue"`): `ENTROPY_RATE_LIMITED`, `QRYPT_RATE_LIMITED`, `QRYPT_SERVER_ERROR`, `QRYPT_ENTROPY_UNAVAILABLE`, `QRYPT_TIMEOUT`, `QRYPT_UNREACHABLE` (`qmc_core.errors.RETRYABLE_ENTROPY_CODES`). Always fail fast: `QRYPT_AUTH_FAILED`, `QRYPT_FORBIDDEN`, `QRYPT_BAD_RESPONSE`, `QRYPT_BAD_REQUEST`, `SECRET_UNAVAILABLE`.

## Data (job item, `JOB#<id>/META`)

- `status = "PENDING_ENTROPY"`, the validated `params`, `paths`, `shards`, `paths_per_shard`, `chunk_size`, `variance_reduction`, `algo_version`, `created_at`, `ttl`, `fault_injection`?, plus `entropy_attempts` (N), `last_entropy_error` (S, a §5.3 code), `pending_until` (N, epoch s). **No** `master_seed_hex`, `seed_provenance` or `deadline_at`; no SQS messages. JOBLIST entry written as usual with `status_at_submit = PENDING_ENTROPY`.

## Builders (`qmc_core.ddb`)

- `new_pending_job_item(*, job_id, params, paths, shards, paths_per_shard, chunk_size, variance_reduction, algo_version, now_epoch, max_age_s, last_error, ttl_days=TTL_DAYS, fault_injection=None) -> dict`; written with the existing `job_put`.
- `pending_attempt_failed(job_id, error_code) -> dict`: `ADD entropy_attempts :1 SET last_entropy_error = :e`, condition `#s = :pending`, `ReturnValues=ALL_NEW`.
- `pending_to_queued(job_id, master_seed_hex, seed_provenance, now_epoch, deadline_s) -> dict`: `SET #s = :queued, master_seed_hex, seed_provenance, deadline_at = now + deadline_s`, condition `#s = :pending`, `ALL_OLD` on failure.
- `pending_fail(job_id, reason) -> dict`: `SET #s = :failed, fail_reason = :why`, condition `#s = :pending`.
- `aggregate.decide` returns `none` for `PENDING_ENTROPY` (the shard deadline reaper never applies).

## Flow (WS2)

Submit with `"queue"` and a retryable failure → if fewer than `MAX_PENDING_ENTROPY` PENDING jobs exist (counted from the newest 50 JOBLIST entries + `job_statuses`), `job_put(new_pending_job_item(...))`, `joblist_put`, async self-invoke `{"qmc_seed": job_id, "attempt": 1}` (`InvocationType=Event`), return 202 `{"job_id", "status": "PENDING_ENTROPY", ...}`; otherwise the original error as today. The seeder (event without `requestContext`): sleep `PENDING_BACKOFF_MS[attempt-1]` bounded by the remaining time, take a gap slot, fetch entropy, entropy claim, derive the seed, `pending_to_queued`, enqueue, metrics (`JobsSubmitted`, `EntropyKiB`, `QryptLatencyMs`); on a retryable failure `pending_attempt_failed` and re-invoke with `attempt + 1` until `PENDING_MAX_ATTEMPTS`, then `pending_fail` + `SeedingFailures`; on a non-retryable failure `pending_fail` at once. GET on a PENDING job past `pending_until` runs `pending_fail` (lost async event backstop). Replay of a PENDING job → 409.

## Env

`PENDING_MAX_ATTEMPTS` 4, `PENDING_BACKOFF_MS` "2000,4000,8000", `PENDING_MAX_AGE_S` 120, `MAX_PENDING_ENTROPY` 10 (API). Terraform: `aws_lambda_function_event_invoke_config` on the API (max retries 0, max event age 300 s); API role `lambda:InvokeFunction` on its own ARN.

## Invariants

I1 reworded: "Seeded only from Qrypt. A seeding failure writes nothing — unless the client opted into `on_entropy_failure: "queue"`, in which case only an unseeded `PENDING_ENTROPY` record is written, and it becomes `QUEUED` only with a Qrypt-derived seed or ends `FAILED` naming the cause." No new metric, alarm, queue or mapping.

## Notes after implementation (2026-10-13)

- **Backoff is clipped by the 10 s API timeout:** `PENDING_BACKOFF_MS` "2000,4000,8000" runs as 2, 4, 6, 6 s (each attempt must still fit the Qrypt deadline and the work reserve).
- **Known limit (accepted, fail-safe):** an abandoned PENDING job past `pending_until` keeps counting toward `MAX_PENDING_ENTROPY` until a GET expires it or it ages out of the newest 50 JOBLIST entries; meanwhile new opt-in submits fail fast as before.
- WS3's review found two seeder gaps (a QUEUED job without messages after a throttled re-read; a duplicate async delivery doubling the retry chain); fixed in MR !44.
