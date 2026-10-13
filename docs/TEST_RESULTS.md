# Test results (WS4)

Evidence for every row of HANDOFF §11. Each entry names the command, the commit or deployment it ran against, and its output. **Pending** means it has not run yet. Final form at the G3 commit (QMC-148).

## Summary

| HANDOFF §11 requirement | Evidence | Result |
| --- | --- | --- |
| Core 1: submit returns an id; record has seed, params, QUEUED | `tests/integration/test_submit.py` (moto) | PASS (local, 2026-10-13) |
| Core 2: distinct, reproducible shard seeds | `tests/correctness/test_seed_independence.py`, `tests/core/test_seeds.py` | PASS; max \|corr\| 0.02805 < 0.05 |
| Core 3: progress then COMPLETE | `make e2e` | PASS (c768aea): `RUNNING:8/10 → COMPLETE:10/10` |
| Core 4: within 3 SE at 1M paths | `tests/correctness/test_closed_form.py` | PASS, z = −2.748 (frozen seed; see below) |
| Core 5: bit-identical replay | `make test-golden`, `tests/integration/test_pipeline.py::test_replay_is_bit_identical`, `make e2e`, `make replay-check N=20` | golden PASS (Docker + CI 52736, both emulated), integration PASS, `make e2e` replay bit-identical PASS (c768aea); ×20 **pending** |
| Core 6: forced failure → DLQ alarm + email | `make warm-dlq`, `make force-failure` | **pending** |
| Fail-closed seeding | `tests/integration/test_submit.py::test_qrypt_failure_rejects_and_writes_nothing`, WS2 unit tests; deployed secret-failure check (below); deployed 401 decoy drill | integration PASS; deployed SECRET_UNAVAILABLE PASS (c768aea); 401 drill **pending** |
| Duplicate SQS delivery | `test_every_message_delivered_twice`, `test_crash_between_shard_put_and_mark_done`; `make force-failure ARGS=--duplicate` | integration PASS; deployed **pending** |
| Worker failure → PARTIAL | `test_forced_failure_ends_partial`, `test_reaper_marks_missing_shard_partial_and_partial_is_final` | integration PASS; deployed **pending** |
| Lambda timeout bound | `tests/deployed` paths-per-shard 400; worker duration from `make bench` | deployed validation PASS; duration **pending** |
| Burst vs 30 req/10 s | `make burst` | **pending** |
| Cost runaway | `tests/deployed` MAX_PATHS 400 | PASS (deployed, c768aea) |
| Invalid inputs | `tests/api/test_validation.py` (WS2); `tests/deployed/test_edge_cases.py` | PASS (deployed, c768aea) |
| Clean teardown, no drift | `make drift-check` (integrator) | **pending** |
| Empty / first run | `test_fresh_deployment_lists_no_jobs`; `make e2e ARGS=--fresh` | integration PASS; deployed 404 PASS; fresh list **pending** |
| Benchmarks | `make bench` → `docs/BENCHMARKS.md` | **pending** |
| Coverage ≥ 80% | GitLab CI pipeline 52736 `unit` job (main ea3820c) | PASS: 96.20% total (gate 80%), qmc_core 100.00% (gate 90%) |

## CI (GitLab pipeline 52736, main ea3820c)

All 7 jobs succeeded: `ssm-data-source-guard`, `lint`, `checkov`, `tflint`, `terraform`, `golden`, `unit`. The `unit` job log shows `320 passed, 25 deselected`, `Required test coverage of 80% reached. Total coverage: 96.20%`, then a qmc_core-only run with `207 passed`, `Required test coverage of 90% reached. Total coverage: 100.00%`.

## Unit and integration (`make test`)

On `ws4-verify` rebased on `main` 974f229 (WS2 and WS3 merged): `319 passed, 25 deselected`. Before that merge, ws4-verify merged with `origin/ws2-api` and `origin/ws3-worker` in a throwaway worktree gave `tests/integration`: `13 passed`.

## Closed form (Core 4)

`uv run pytest tests/correctness -s`, frozen seed `sha256("qmc-closed-form-test-v1")`, 1,000,000 paths, 10 shards:

```
estimate=10.41020449467039 se=0.014693164677226917 z=-2.7482
```

z = −2.748 is inside 3 SE. The seed was frozen before the first run and is never changed; with a fixed seed this test is deterministic and fails for about 0.27% of seeds. James has been told.

## Golden hash (Core 5, I5)

`make test-golden` (public.ecr.aws/lambda/python:3.13, linux/amd64 under Docker on Apple silicon, CPU "VirtualApple", flags avx2 fma, NumPy 2.5.4, `NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR"`): `7 passed`, exp target `X86_V3`.

```
GOLDEN_SUM_HEX = "0x1.fdcf3646e51c2p+19"
GOLDEN_SUM_SQ_HEX = "0x1.f2c02fc1b1bcep+24"
```

GitLab CI pipeline [52736](https://labs.gauntletai.com/jamesmcgirr/quantum-seeded-monte-carlo/-/pipelines/52736) on `main` ea3820c, job `golden`: `7 passed, 338 deselected`. That includes `test_golden_shard` and `test_exp_target_is_not_avx512`. The runner is James's Mac, so this is also the Lambda image under amd64 emulation, not native x86.

Status: **checked under emulation and against real Lambda x86 by the e2e local recompute** (see `make e2e`). **Provisional:** they come from emulation. They become authoritative when a real x86 run agrees (a CI golden job, or `make e2e`'s local-recompute line against Lambda). Native macOS arm64 happens to give the same bits for this shard; that is not a guarantee.

## Deployed edge cases (QMC-143), build_sha c768aea

`AWS_PROFILE=qmc uv run pytest -m deployed tests/deployed -v`. No Qrypt spent: every request is rejected before the limiter.

```
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change0-fields0] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change1-fields1] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change2-fields2] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change3-fields3] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change4-fields4] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change5-fields5] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change6-fields6] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change7-fields7] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change8-fields8] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change9-fields9] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change10-fields10] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change11-fields11] PASSED
tests/deployed/test_edge_cases.py::test_invalid_inputs_are_400_with_clear_details[change12-fields12] PASSED
tests/deployed/test_edge_cases.py::test_not_json_and_not_object PASSED
tests/deployed/test_edge_cases.py::test_unknown_job_404_names_id PASSED
tests/deployed/test_edge_cases.py::test_malformed_id_is_404_not_500 PASSED
tests/deployed/test_edge_cases.py::test_unknown_route_and_wrong_method PASSED
tests/deployed/test_edge_cases.py::test_fail_closed_bad_token SKIPPED
SKIPPED [1] tests/deployed/test_edge_cases.py:81: fail-closed drill only
======================== 17 passed, 1 skipped in 3.03s =========================
```

`GET /health` at that time: `503 {"checks": {"dynamodb": "ok", "ssm": "error: ParameterNotFound"}, "build_sha": "c768aea…"}`: the Qrypt token parameter (HANDOFF Day 0 step 0.4) had not been created yet.

## `make e2e` (QMC-142), build_sha c768aea, 2026-10-13 11:26 UTC

`AWS_PROFILE=qmc make e2e`, job `edaff921aad7466490c53c4fcc4495da`, replay `d6afc05874624db98a8b67710f56f2c2`. Qrypt: 1 KiB.

| check | result | detail |
| --- | --- | --- |
| health 200                        | PASS | build_sha=c768aea3023263d046fadfb3e3fa9f64f5ddd2ae checks={'dynamodb': 'ok', 'ssm': 'ok'} |
| GET /jobs is an array             | PASS | 2 jobs |
| unknown id → 404                  | PASS | {'code': 'JOB_NOT_FOUND', 'message': "No job with id '00000000000000000000000000000000'."} |
| submit 1M/10 → 202                | PASS | edaff921aad7466490c53c4fcc4495da |
| progress observed before terminal | PASS | RUNNING:8/10 → COMPLETE:10/10 |
| COMPLETE                          | PASS | 2.7 s submit → COMPLETE |
| within 3 SE of Black-Scholes      | PASS | estimate=10.427494563625112 se=0.01470142633138016 bs=10.450583572185565 z=-1.5705284671031579 |
| 1M paths counted once             | PASS | 1000000 |
| replay → 202                      | PASS | d6afc05874624db98a8b67710f56f2c2 |
| replay COMPLETE                   | PASS | COMPLETE |
| replay bit-identical              | PASS | shards_compared=10 diffs=[] fingerprint_match=True |

11/11 passed

```
INFO  local recompute (arm64) matches Lambda: 0x1.4dae09143f9adp+3 vs 0x1.4dae09143f9adp+3
```

- Lambda fingerprint (both jobs): python 3.13.15, numpy 2.5.4, glibc 2.34, x86_64, exp_target X86_V3, fma true, avx2 true, avx512f false, dispatch_enforced true.
- Shard `cpu_model`s: `Intel(R) Xeon(R) Processor @ 2.50GHz` (9 shards) and `Intel(R) Xeon(R) Processor` (1). Shard durations were 3–5 ms.
- Cross-check of the provisional golden constants: real Lambda x86 (X86_V3) produced exactly the bits that native arm64 computes for this job, and Docker-emulated x86 produced exactly the arm64 bits for the golden shard.
- **Risk, "progress observed":** a 1M/10 job completes in about 1–3 s. e2e makes its first GET immediately after the 202, in the same process, and saw `RUNNING:8/10`. WS2's separate-process `wait` saw COMPLETE on its first poll. A rerun can therefore miss the non-terminal state; if it does, the check fails visibly and is recorded, never loosened.
- WS2's G1 run, for the record: job `5358ec35…` COMPLETE, z = 1.735; replay `f1df7275…` bit-identical, 10 shards, 0 diffs; submit REPORT 488.30 ms (Qrypt 310 ms on the first TLS connection). Commands are in `docs/ai-log/WS2.md` on `ws2-api`.

## Fail closed on the live stack: secret failure (I1, I6), build_sha c768aea

WS2 ran the checks at 11:17 CT (commands and outputs in `docs/ai-log/WS2.md` on `ws2-api`, section "live checks"). The SSM parameter `/qmc/qrypt-api-key` did not exist yet, so this is the secret-failure form of I1. The Qrypt 401 decoy drill is still pending.

- `POST /jobs` (1M/10 reference) → `503 SECRET_UNAVAILABLE`; then `GET /jobs` → `200 {"jobs": []}`: nothing written.
- Verified by WS4 with `FilterLogEvents` on `/aws/lambda/qmc-dev-api` (last 6 h):
  - 2 EMF lines `{"_aws": {..."Namespace": "QMC/dev", "Dimensions": [[]], "Metrics": [{"Name": "SeedingFailures", "Unit": "Count"}]}], "SeedingFailures": 1, "cause": "SECRET_UNAVAILABLE", ...}` at 11:17:44.401Z and 11:17:45.519Z;
  - 2 matching `{"event": "seeding_failed", "cause": "SECRET_UNAVAILABLE", ...}` lines (request ids d8110d22…, 7fc5dcbb…).
- I6 (no secret in logs): over the last 6 h, 0 events in `qmc-dev-api` or `qmc-dev-worker` for each of the filter terms `"Authorization"`, `"Bearer"`, `authorization`, `token`, `Token`, `api_key`.
- Natural cold starts (API REPORT lines): `Init Duration: 83.84 ms` and `295.72 ms`.

## Qrypt spend (WS4)

| Run | KiB |
| --- | --- |
| deployed edge cases | 0 |
| make e2e (11:26 UTC) | 1 |
| **Total** | **1** |
