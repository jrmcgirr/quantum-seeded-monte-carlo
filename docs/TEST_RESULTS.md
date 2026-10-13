# Test results (WS4)

Evidence for every row of HANDOFF §11. Each entry names the command, the commit or deployment it ran against, and its output. **Pending** means it has not run yet. Final form at the G3 commit (QMC-148).

## Summary

| HANDOFF §11 requirement | Evidence | Result |
| --- | --- | --- |
| Core 1: submit returns an id; record has seed, params, QUEUED | `tests/integration/test_submit.py` (moto) | PASS (local, 2026-10-13) |
| Core 2: distinct, reproducible shard seeds | `tests/correctness/test_seed_independence.py`, `tests/core/test_seeds.py` | PASS; max \|corr\| 0.02805 < 0.05 |
| Core 3: progress then COMPLETE | `make e2e` | PASS (c768aea): `RUNNING:8/10 → COMPLETE:10/10` |
| Core 4: within 3 SE at 1M paths | `tests/correctness/test_closed_form.py` | PASS, z = −2.748 (frozen seed; see below) |
| Core 5: bit-identical replay | `make test-golden`, `tests/integration/test_pipeline.py::test_replay_is_bit_identical`, `make e2e`, `make replay-check N=20` | golden PASS (emulated GitLab 52736, native x86 GitHub run 38392842904, Lambda per-shard), integration PASS, `make e2e` replay bit-identical PASS (c768aea); ×20 **pending** |
| Core 6: forced failure → DLQ alarm + email | `make warm-dlq`, `make force-failure ARGS="--purge-first --duplicate"` | PASS (f40a083): PARTIAL [3] +13 s, DLQ 1 +23 s, OK→ALARM +237 s, SNS publish OK; email screenshot **pending** (James) |
| Fail-closed seeding | `tests/integration/test_submit.py::test_qrypt_failure_rejects_and_writes_nothing`, WS2 unit tests; deployed secret-failure check (below); deployed decoy-token drill | integration PASS; deployed SECRET_UNAVAILABLE PASS (c768aea); decoy drill: **fails closed (503, nothing written, alarm) but FAIL on the cause**: Qrypt returns 403, reported as QRYPT_QUOTA_EXHAUSTED (finding below) |
| Duplicate SQS delivery | `test_every_message_delivered_twice`, `test_crash_between_shard_put_and_mark_done`; `make force-failure ARGS=--duplicate` | integration PASS; deployed PASS (f40a083, `duplicate_delivery` logged, result and n unchanged) |
| Worker failure → PARTIAL | `test_forced_failure_ends_partial`, `test_reaper_marks_missing_shard_partial_and_partial_is_final`; `make force-failure` | integration PASS; deployed PASS (f40a083) |
| Lambda timeout bound | `tests/deployed` paths-per-shard 400; worker duration from `make bench` | deployed validation PASS; duration **pending** |
| Burst vs 30 req/10 s | `make burst` | PASS (d0af0b1): 40 concurrent → 15 × 202, 25 × 429 with Retry-After; 15 limiter slots in the window; max 15 Qrypt calls in any rolling 10 s; SampleCount = log lines |
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

Status: **confirmed** in three independent environments:
1. Emulated amd64: `make test-golden` and GitLab CI pipeline 52736 (`golden`, 7 passed).
2. Native x86_64: GitHub Actions run [38392842904](https://github.com/jrmcgirr/quantum-seeded-monte-carlo/actions/runs/38392842904) on `main` d0af0b1, job `golden` on a GitHub-hosted `ubuntu-24.04` runner, `success`; all 7 jobs green. Verified with the public GitHub API.
3. Real Lambda x86: per-shard cross-check (below).

### Real Lambda x86 vs emulated x86 vs arm64, per shard (WS1's risk)

The concern: Rosetta-emulated x86 might dispatch or round differently from real Lambda hardware. The check reuses e2e job `edaff921…`, whose 10 shards (100k paths each, the golden shard's size) ran on real Lambda: `Intel(R) Xeon(R) Processor [@ 2.50GHz]`, exp_target X86_V3, dispatch enforced. The tool reads their stored `sum_hex`/`sum_sq_hex` and recomputes every shard from the stored master seed:

```
AWS_PROFILE=qmc uv run python tools/shard_crosscheck.py fetch edaff921aad7466490c53c4fcc4495da > job.json
uv run python tools/shard_crosscheck.py check job.json                       # native macOS arm64
docker run … public.ecr.aws/lambda/python:3.13 … tools/shard_crosscheck.py check job.json   # emulated x86 (see tool docstring)
```

```
edaff921aad7466490c53c4fcc4495da: 10/10 shards bit-identical; here = arm64 exp=baseline(NEON NEON_FP16 NEON_VFPV4 ASIMD) cpu=arm
edaff921aad7466490c53c4fcc4495da: 10/10 shards bit-identical; here = x86_64 exp=X86_V3 cpu=VirtualApple @ 2.50GHz
```

Every `sum_hex` and `sum_sq_hex`, 20 values, is identical across real Lambda x86, Rosetta-emulated x86 and native arm64. The environment that generated the golden constants therefore produces Lambda's bits for this workload. Native macOS arm64 happens to give the same bits; that is a bonus, not a guarantee.

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

## DLQ drill and duplicate delivery (QMC-144), build_sha f40a083, 2026-10-13

- `make warm-dlq` at 11:26:20Z.
- `make force-failure ARGS="--purge-first --duplicate"`, started 11:41:30Z (15 min 10 s after the warm-up). Forced-failure job `53d3b3e04a8f4b678285c1db7ea17e3b`; duplicate-drill job `84a31793e504490998ac42dc171be9a7`. Qrypt: 2 KiB.
- Setup (approved by WS0): WS3's G1 forced failure had left 1 message in the DLQ and the alarm was ALARM. The purge brought the DLQ to 0 and the alarm to OK after 168 s, so the drill shows a fresh transition with depth exactly 1.

| check | result | detail |
| --- | --- | --- |
| precondition: DLQ empty, alarm not ALARM | PASS | depth=0 alarm=OK |
| submit with fail_shards=[3] → 202        | PASS | 53d3b3e04a8f4b678285c1db7ea17e3b |
| job PARTIAL with [3]                     | PASS | PARTIAL failed=[3] at +13 s |
| no result, partial over 9 shards         | PASS | {'estimate': 10.488724787774899, 'std_error': 0.049279063656201515, 'ci95': [10.392139597816888, 10.58530997773291], 'n': 90000, 'estimate_hex': '0x1.4fa3a22a879b8p+3', 'std_error_hex': '0x1.93b1afd8fd083p-5', 'ci95_hex': ['0x1.4c8c685782e25p+3', '0x1.52badbfd8c54bp+3']} |
| DLQ depth 1                              | PASS | 1 at +23 s |
| qmc-dev-dlq-depth ALARM                  | PASS | at +237 s after submit |
| duplicate drill: submit → 202            | PASS | {'job_id': '84a31793e504490998ac42dc171be9a7', 'status': 'QUEUED', 'paths': 100000, 'shards': 4, 'master_seed_hex': '39967f3e4e4dfeba4496f748c36d01098d0f6dada0928b33cf79070a6be30abc', 'links': {'self': '/jobs/84a31793e504490998ac42dc171be9a7', 'replay': '/jobs/84a31793e504490998ac42dc171be9a7/replay'}} |
| duplicate drill: COMPLETE                | PASS | COMPLETE |
| worker logged duplicate_delivery         | PASS | after 11 s |
| result and n unchanged                   | PASS | {'estimate': 10.485852909283137, 'std_error': 0.046811763589818804, 'ci95': [10.394103538594289, 10.577602279971986], 'n': 100000, 'estimate_hex': '0x1.4f8c1b6681337p+3', 'std_error_hex': '0x1.7f7b6235f69f7p-5', 'ci95_hex': ['0x1.4c9c7f063002dp+3', '0x1.527bb7c6d2641p+3']} |

10/10 passed

`aws cloudwatch describe-alarm-history --alarm-name qmc-dev-dlq-depth` (from 11:40Z):

```
11:44:08.034Z StateUpdate  Alarm updated from ALARM to OK          (setup purge)
11:44:08.075Z Action       Successfully executed action arn:aws:sns:us-east-2:…:qmc-dev-alerts
11:48:08.033Z StateUpdate  Alarm updated from OK to ALARM           (this drill)
11:48:08.079Z Action       Successfully executed action arn:aws:sns:us-east-2:…:qmc-dev-alerts
```

Timeline from submit: PARTIAL with `[3]` at +13 s (forced failure, 3 receives at 5 s visibility, R14); DLQ depth 1 at +23 s; alarm ALARM at +237 s (SQS metric lag, R23). The DLQ was purged at the end, so an OK email follows. **Email screenshot:** pending from James, for WS5.

## Burst (QMC-143), build_sha d0af0b1, 2026-10-13 12:01:50Z

`AWS_PROFILE=qmc make burst`: 40 concurrent `POST /jobs` (1,000 paths, 1 shard each).

```
40 submits in 4.01 s: {429: 25, 202: 15} {'ENTROPY_RATE_LIMITED': 25}
PASS  only 202 or 429 ENTROPY_RATE_LIMITED  {'ENTROPY_RATE_LIMITED': 25}
PASS  every 429 has Retry-After  
PASS  limiter: ≤ 15 slots per 10 s window  {1791892910: 15}
PASS  qrypt_call lines ≥ accepted jobs  15 calls, 15 jobs
PASS  ≤ 30 Qrypt calls in any rolling 10 s  max 15
qrypt_call per fixed window (log timestamps): {1791892910: 15}
FAIL  QryptLatencyMs SampleCount = qrypt_call lines  20 (after 0 s)
```

- The limiter item `RL#qrypt/1791892910` (window starting 12:01:50Z) has `n = 15`: the cap of HANDOFF D10.
- 15 `qrypt_call` lines, all in that window; the most in any rolling 10 s is 15, against the assignment's 30.
- The one FAIL was a tool bug, not a system fault. The SampleCount query started 60 s before the burst, so it also counted the 5 calls from an aborted first attempt (below), giving 20 against 15. The tool now compares the metric and the log lines over the same whole-minute buckets. Re-checked on the same data with no new submits: `burst minute 12:01: (20, 20)`.
- Aborted first attempt at 12:01:11Z: the 40 threads raced 40 concurrent `make outputs` (`terraform init`), and the tool crashed after 5 submits had gone through (5 × Qrypt 200, 5 KiB). Fixed in `tools/_common.py` (thread-safe `outputs()`). Those 5 calls also showed the real token still had quota after the 403 finding.

## Fail-closed drill with a decoy token (QMC-143), build_sha d0af0b1, 2026-10-13

Sequence:
1. WS4 created the SecureString `/qmc/qrypt-api-key-invalid` at 11:50:07Z, with an obviously fake value.
2. The integrator applied `-var qrypt_api_key_param_name=/qmc/qrypt-api-key-invalid` (live 11:50:43Z); `/health` → 200, ssm ok.
3. WS4 ran `QMC_DRILL=fail-closed uv run pytest -m deployed tests/deployed/test_edge_cases.py::test_fail_closed_bad_token -s` twice. Qrypt: 0 KiB.

```
submit with the decoy token → 503 {'error': {'code': 'QRYPT_QUOTA_EXHAUSTED', 'message': 'Qrypt entropy quota exhausted (403).'}} at 11:51:10Z
E       AssertionError: assert (503 == 503 and 'QRYPT_QUOTA_EXHAUSTED' == 'QRYPT_AUTH_FAILED'
```

API log (`FilterLogEvents`, `/aws/lambda/qmc-dev-api`):

```
{"ts":"2026-10-13T11:51:04.994Z","event":"qrypt_call","job_id":"ea88b6a3…","http_status":403,"latency_ms":203,"attempt":1,"outcome":"QRYPT_QUOTA_EXHAUSTED"}
{"_aws":{…"SeedingFailures"…},"SeedingFailures":1,"cause":"QRYPT_QUOTA_EXHAUSTED","job_id":"ea88b6a3…"}
{"ts":"2026-10-13T11:51:10.462Z","event":"qrypt_call","job_id":"5b11e097…","http_status":403,"latency_ms":86,"attempt":1,"outcome":"QRYPT_QUOTA_EXHAUSTED"}
```

- **Holds:** both submits got 503 and no job was written (neither id is in `GET /jobs`; `GET /jobs/{id}` → 404 for both). The 403 was not retried, per §7.5. `qmc-dev-seeding-failures` went OK → ALARM at 11:52:32Z.
- **Finding (FAIL, reported to WS0):** Qrypt answers an invalid token with **HTTP 403, not 401**. Under HANDOFF §5.3, 403 maps to `QRYPT_QUOTA_EXHAUSTED`, so an invalid or expired token is reported to the operator as an exhausted quota: the wrong cause. The assertion has not been loosened; the fix is a contract decision (WS0).

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
| make force-failure --duplicate (11:41 UTC) | 2 |
| fail-closed decoy drill (11:51 UTC, Qrypt 403) | 0 |
| burst: aborted attempt (12:01:11) + run (12:01:50) | 5 + 15 |
| **Total** | **23** |
