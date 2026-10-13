# WS4 — Verification

You are building the proof. The rubric gives points for committed closed-form, determinism and idempotency tests, and every edge case in the assignment is "an observable expectation a reviewer can check". Your job is to make each requirement checkable by a command, to show that replay is bit-identical on real Lambda hardware (not just on a laptop), and to record evidence the README can cite. You contribute to G1 (correctness tests and `make e2e`, **Tuesday 6:00 PM Central**), lead G2 (drills on the deployed stack, **Wednesday 12:00 PM**) and G3 (benchmarks and TEST_RESULTS, **Wednesday 4:00 PM**).

**Start after:** G0a (tag `g0a-interfaces`, about 9:45 AM); the correctness tests need only `qmc_core`, so write them against the interfaces and they go green at G0 (noon). The golden test waits for the NumPy pin (QMC-099, 2:00 PM). Work on branch `ws4-verify` in `../quantum-seeded-monte-carlo-ws4`.

## Read first

1. `docs/HANDOFF.md`: §11 maps every assignment requirement to a test or tool; it is your specification. Also §1 (invariants), §5 (API), §7 (semantics), §9.2 (alarms), §14 (risks).
2. `docs/ASSIGNMENT.md`: "Edge Cases & Failure Modes" and "Performance Benchmarks", word for word.
3. `docs/PRIMER.md`, the risk table.
4. `CLAUDE.md`; `qmc_core`; `clients/qmc.py` once WS2 ships it.
5. Your tickets in `docs/ROADMAP.md`: QMC-140 to QMC-148.

## You own

`tests/correctness/**`, `tests/integration/**`, `tests/deployed/**`, `tools/**`, `docs/TEST_RESULTS.md`, `docs/BENCHMARKS.md`, `docs/ai-log/WS4.md`. You change no service or infra code. When you find a bug, open a GitLab issue for the owning workstream with the failing command and tell James. **You never apply or destroy** (HANDOFF D33): `tools/drift_check.sh` is yours, but the integrator runs it from `main`; drills that need a configuration change are applied by the integrator with `make apply EXTRA_VARS='-var …'` while you run the assertions. Never change Lambda configuration with the AWS CLI (it causes drift and replaces the whole environment map).

## Build in this order

1. **Correctness (QMC-140).** `test_closed_form.py`: frozen seed, 1M paths, 10 shards, within 3 SE of 10.450583572185565 (print z). `test_seed_independence.py`: distinct seeds for 1,000 indexes; reproducibility; max pairwise correlation of 20 shard streams × 10k normals below 5/√n. `test_golden.py` (`lambda_image` marker): one 100k-path shard from a fixed seed must equal committed `sum_hex`/`sum_sq_hex`, and the `exp` target must not be AVX-512. Generate the constants once in CI's `golden` job (or `make test-golden` in Docker), commit them, never regenerate without James.
2. **Integration (QMC-141).** In process, against moto, with `FakeEntropySource`: build Function URL events for the API handler, pump the SQS messages into the worker handler (in random order, duplicated, with a crash injected between writes), then `GET` and replay. These are the idempotency and PARTIAL tests the rubric asks for, runnable with no AWS account.
3. **`make e2e` (QMC-142).** `tools/e2e.py` on the deployed stack via `clients/qmc.py`: health (print `build_sha`); `GET /jobs` returns an array (with `--fresh`, right after an apply from empty, it must be empty); unknown id → 404; the 1M-path reference job, recording at least one non-terminal observation (Core 3's "shows progress"), to COMPLETE; `within_3se`; replay; `bit_identical`. A summary table; non-zero exit on failure; rerunnable after every merge.
4. **Edge cases on the deployed stack (QMC-143).** Validation spot checks; 404; the fail-closed drill (you create the decoy SecureString `/qmc/qrypt-api-key-invalid` by CLI; the integrator applies with `EXTRA_VARS='-var qrypt_api_key_param_name=/qmc/qrypt-api-key-invalid'`; you assert 503 `QRYPT_AUTH_FAILED`, nothing listed, the seeding alarm fires; the integrator re-applies normally and plan = 0; you delete the decoy); `make burst` (40 concurrent submits → at most 15 Qrypt calls per 10 s window; count calls from the API logs' `qrypt_call` lines and `QryptLatencyMs` sample counts).
5. **Failure drills (QMC-144).** `make warm-dlq` (`tools/warm_dlq.sh`: any SQS call on the DLQ) at least 15 minutes before, because SQS metrics for an inactive queue can take up to 15 minutes to appear. Then `make force-failure`: PARTIAL with `[3]`, DLQ depth 1, alarm ALARM, email; time each step. Duplicate drill with `aws sqs send-message` of a completed shard's body. Purge the DLQ afterwards.
6. **Drift and teardown (QMC-145).** `tools/drift_check.sh`, run by the integrator from `main` in the G2 window: plan = 0 → destroy → orphan scan (tagging API, `/aws/lambda/qmc-*` log groups, SNS subscriptions, the table) → apply (timed) → `make e2e --fresh` → plan = 0 → James confirms the new SNS subscription.
7. **Replay ×20 (QMC-146).** Four batches of five concurrent replays, batches at least 10 minutes apart so execution environments are recycled; poll every 2 s; record `cpu_model` per shard and the log stream names; all bit-identical. No configuration changes to force cold starts.
8. **Benchmarks (QMC-147).** `tools/bench.py`: 50 warm submissions (small jobs) paced at least 700 ms start-to-start so our own limiter (15 per 10 s) is not hit, p50/p95 from the client and from REPORT lines, any 429 counted and reported; cold starts from natural ones (first request after ≥ 15 minutes idle, and REPORT lines with `Init Duration` over the whole build); five 1M-path runs submit → COMPLETE; apply time. `docs/BENCHMARKS.md` with method and environment.
9. **Evidence (QMC-148).** `docs/TEST_RESULTS.md`: command, output, pass/fail for every row of HANDOFF §11 at the G3 commit; coverage per package.

## Requirements that are easy to get wrong

- **Qrypt quota is finite and unpublished.** Every live submit spends 1 KiB. Use small `paths` where the size of the job does not matter (latency benchmarks), and keep the whole suite under about 200 KiB. Never loop submits without a cap.
- The burst test must prove the cap is honoured, not just that some 429s came back: count Qrypt calls per 10 s window.
- The DLQ alarm uses SQS metrics; for an inactive queue (the DLQ always is) the first metrics can take up to 15 minutes. Warm the DLQ first, poll the alarm with a 20-minute timeout and record the time; do not assert immediacy.
- Poll at 2 s, never faster: the table has 10 RCU and consistent reads cost double.
- The golden constants come from the Lambda image, not from your laptop (macOS arm64 uses a different `exp`). Local runs skip the `lambda_image` tests.
- A fixed-seed "within 3 SE" test is deterministic: it fails for about 0.27% of seeds. The seed is frozen once; never shop for a passing seed. If it fails, report it to James.
- Treat every failure as a finding about the system first. Never loosen an assertion to make it pass without telling James.
- Don't leave the stack in a broken state: restore the real Qrypt parameter name and purge the DLQ after drills.

## Done when

- `make test` (unit + integration), CI's `golden` job, `make e2e`, the drills, `make drift-check`, `make replay-check N=20` and `make bench` are green or recorded.
- `docs/TEST_RESULTS.md` and `docs/BENCHMARKS.md` are complete and every HANDOFF §11 row has evidence.
- You report: every bug found (with its issue), the benchmark numbers against the targets, the Qrypt KiB spent, and an AI usage log entry in `docs/ai-log/WS4.md`.

## Review checklist for James

- [ ] Golden constants were generated in the Lambda image and committed once
- [ ] Integration tests cover duplicate delivery, crash-between-writes and PARTIAL with no AWS account
- [ ] The burst test counts Qrypt calls per window
- [ ] The DLQ drill warmed the DLQ first, shows the email arriving (screenshot) and purged the DLQ afterwards
- [ ] e2e observes a non-terminal status before COMPLETE and passes when rerun
- [ ] The drift check found zero orphans and two zero-change plans
- [ ] Replay ×20 lists the distinct CPU models it ran on
