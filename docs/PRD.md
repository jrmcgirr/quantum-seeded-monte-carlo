# Quantum-Seeded Monte Carlo — Product Requirements

Version 1.0, 2026-10-12. Owner: James McGirr. Engineering contract: `docs/HANDOFF.md`. Plan: `docs/ROADMAP.md`. Research: `docs/PRIMER.md`. Assignment: `docs/ASSIGNMENT.md`.

## 1. Summary

Quant teams price options and estimate risk with Monte Carlo simulation, and model-risk and audit functions require that any reported number can be reproduced. We build a serverless, Terraform-managed platform on AWS that prices a European call under Black-Scholes. Every job is seeded from Qrypt quantum entropy, fanned out across parallel Lambda workers with independently derived shard seeds, and aggregated into an estimate with a standard error and a 95% confidence interval. Any past job can be replayed bit for bit from its stored seed, with a byte-level comparison as proof. The platform costs $0 at rest and at 100 jobs a day.

## 2. Problem

- **Weak or reused seeds.** Seeds taken from the clock, a shared global RNG or `seed + worker_id` make "independent" runs correlated, and nobody can say where a seed came from.
- **Unreproducible runs.** A number reported last quarter cannot be regenerated because the seed, the parameters, the sharding or the software environment were not recorded. Model validation turns into days of back-and-forth.
- **Hand-built infrastructure.** Console-built environments drift, cannot be rebuilt identically and cannot explain their bill.

## 3. Users

| User | Goal | What they need from us |
| --- | --- | --- |
| Quant analyst | Price an option and trust the number | Submit parameters, get an estimate with SE, CI and the closed-form comparison in under a minute |
| Platform operator | Deploy, monitor, control cost | One `terraform apply`, clean destroy, alarms and a Budget that fire before money is spent |
| Auditor / model validator | Confirm a past result | Replay any job from its stored seed and see a byte-level comparison and the seed's provenance |
| Grader (this take-home) | Check every requirement and edge case | A quick-start that works in their account, a deployed endpoint, committed tests and recorded evidence |

## 4. Goals and non-goals

Goals:

1. Seed every job only from Qrypt; reject the job, naming the cause, when Qrypt or the secret is unavailable (I1).
2. Derive independent, reproducible shard seeds from the master seed and shard index (I2).
3. Count every shard exactly once under duplicate delivery; report PARTIAL, never a wrong COMPLETE, when shards fail (I3, I4).
4. Price within 3 standard errors of Black-Scholes at 1M paths.
5. Replay any completed job bit for bit and prove it (I5).
6. Keep the Qrypt token out of Terraform state, the repo and the logs (I6).
7. Stay at $0 within always-free allowances, with guardrails (I7).

Non-goals (README "Limitations" explains each): Step Functions orchestration; queued retry when entropy is unavailable (PENDING_ENTROPY); ephemeral per-branch environments; portfolio Value-at-Risk; option types other than the European call; a web UI; multi-region or multi-account deployment; deploys from CI.

## 5. Functional requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-1 | `POST /jobs` accepts `{S0, K, r, sigma, T, paths, shards}`, validates it, fetches one Qrypt block, derives a 256-bit master seed, records the job (seed hex, parameters, `QUEUED`) and enqueues one message per shard; returns the job id | Must |
| FR-2 | Shard seeds derive deterministically from `(master seed, shard index)`, never from time or a shared RNG; distinct and reproducible (committed test) | Must |
| FR-3 | Workers simulate their share and write `{sum, sum_sq, n}` per shard; when all shards are done the job is aggregated into estimate, SE and 95% CI; `GET /jobs/{id}` shows progress then `COMPLETE` | Must |
| FR-4 | The reference case at 1M paths is within 3 SE of the closed form (committed test and a deployed run); every result shows the comparison | Must |
| FR-5 | `POST /jobs/{id}/replay` re-runs from the stored seed and parameters; the result is bit-identical and the response proves it | Must |
| FR-6 | Terraform creates a $1/month Budget with alerts, custom metrics (jobs submitted, paths simulated, entropy KiB, shard failures) and alarms (worker errors, DLQ depth > 0, and more); a forced worker failure trips the DLQ alarm and notifies | Must |
| FR-7 | Qrypt failures (401/403/429/500/503/timeout) reject the job with an error naming the cause; never a local or time-based seed | Must |
| FR-8 | Duplicate SQS delivery never double-counts paths | Must |
| FR-9 | A shard that exhausts retries lands in the DLQ and the job is `PARTIAL` with the failed indexes | Must |
| FR-10 | Oversized `paths/shards` ratios and `paths` above the maximum are rejected at submit; workers have reserved and maximum concurrency | Must |
| FR-11 | Bursts of submissions never exceed Qrypt's 30 requests / 10 s | Must |
| FR-12 | Invalid inputs return 400 with a clear message; unknown job id returns 404; `GET /jobs` on a fresh deployment returns an empty list | Must |
| FR-13 | `terraform destroy` removes everything; a second plan after apply shows no drift | Must |
| FR-14 | `GET /health` checks DynamoDB and SSM reachability | Must |
| FR-15 | CloudWatch dashboard | Should (stretch) |
| FR-16 | Checkov security scan in CI with justified suppressions | Should (stretch) |
| FR-17 | Per-job cost attribution in the job result | Could (stretch) |
| FR-18 | Antithetic variates as an opt-in variance reduction | Could (stretch) |

## 6. Success metrics

| Metric | Target | How measured |
| --- | --- | --- |
| Closed-form accuracy | |z| ≤ 3 at 1M paths | `tests/correctness/test_closed_form.py`; `make e2e` |
| Replay | 100% bit-identical, including ×20 across Lambda hosts | `make e2e`; `make replay-check N=20` |
| Duplicate counting | 0 double-counted shards under duplicate and crash-between-writes delivery | Integration tests; deployed duplicate drill |
| Wrong COMPLETE | 0 | Forced-failure drill; reaper tests |
| Seeding without Qrypt | 0 jobs | Fail-closed unit tests; deployed invalid-token drill |
| Qrypt rate | ≤ 30 requests per rolling 10 s | `make burst` |
| Submit latency (warm) | p95 < 800 ms over 50 submissions | `make bench` |
| Job wall-clock | 1M paths < 60 s, 5 runs | `make bench` |
| Environment rebuild | `terraform apply` from empty < 10 min | `make drift-check` timing |
| Steady-state cost | $0 at 100 jobs/day of 1M paths | `docs/COST.md` arithmetic + Budget at $0 |
| Test coverage | ≥ 80% overall, ≥ 90% `qmc_core` | CI |
| Drift | 0 changes on a second plan; 0 orphans after destroy | `make drift-check` |

## 7. Release criteria

All gates in HANDOFF §2 met by Wednesday 2026-10-14, 9:00 PM Central; every *never cut* ticket closed; video recorded; fresh-clone rehearsal passed; stack deployed with its URL in the README; GitHub mirror current; submitted before midnight.
