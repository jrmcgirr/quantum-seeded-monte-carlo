# WS0 — Foundation and contract

You are building the foundation the other workstreams depend on: the repository skeleton, the CI pipeline, and `qmc_core`, the pure package that holds every rule of the system (validation, seed derivation, simulation, aggregation, DynamoDB expressions, the SQS message, error codes, metrics and logging). Then you freeze the contract so the API, worker and verification sessions can work in parallel. Two deadlines govern you: **G0a, Tuesday 2026-10-13 at 9:45 AM Central** (skeleton, final Makefile and `qmc_core` interface stubs merged to `main`, tag `g0a-interfaces`), when WS2, WS3 and WS4 start coding against your interfaces; and **G0, 12:00 PM** (implementations, tests, CI green, tag `g0-contract-frozen`). The NumPy/SIMD pin follows by 2:00 PM (QMC-099). WS1 (Terraform) and WS5 (docs) are already running from 9:00 AM.

**Start:** Tue 9:00 AM, in the main checkout, on branch `ws0-foundation`. After G0 you stay on as integrator (and the person who runs `make apply` from `main`).

## Read first

1. `docs/HANDOFF.md`, all of it. Sections 4 (layout), 5.2–5.3 (validation, errors), 6 (data model and expressions), 7 (semantics), 9.1 and 9.3 (EMF, logs) and 12 (pins) are your specification.
2. `docs/PRIMER.md`, sections "Pricing the option is trivial; reproducing it bit-for-bit is not" and "One queue, one table, and conditional writes carry correctness".
3. `CLAUDE.md` and `docs/ROADMAP.md` (your tickets: QMC-099 to QMC-109, QMC-119).

## You own

Root files except `README.md` and `AI_USAGE.md`; `src/qmc_core/**`, `tests/core/**`, `requirements/**`, `.gitlab-ci.yml`, `.github/workflows/**`, `.gitattributes`, `scripts/bootstrap.sh`, `scripts/*gitlab*`, `scripts/issue_map.csv`, `docs/HANDOFF.md`, `docs/ROADMAP.md`, `docs/prompts/**`, `docs/contract-changes/**`, `docs/ai-log/WS0.md`, `CLAUDE.md`.

## Build in this order

1. **G0a (QMC-100), merged to `main` by 9:45 AM.** `pyproject.toml` with the `src/` layout (`qmc_core`, `qmc_api`, `qmc_worker`), ruff and pytest configuration (markers `deployed`, `lambda_image`), every dev dependency pre-declared (HANDOFF §4), a provisional NumPy pin; stub `qmc_api/handler.py` and `qmc_worker/handler.py` with a `handle(event, context)` that returns a fixed 200 JSON, so WS1 can build and deploy before G0; **`qmc_core` interface stubs** (every public function and dataclass named in CLAUDE.md, with signatures, types and docstrings, raising `NotImplementedError`), so WS2, WS3 and WS4 can code against them; the **final Makefile** (recipes call the fixed script paths in HANDOFF §4; `plan`/`apply`/`destroy` use the state path, `-var-file=$HOME/.qmc/$(ENV).tfvars` and `$(EXTRA_VARS)`; `apply`/`destroy` refuse a checkout whose `HEAD` does not contain `origin/main` unless `ALLOW_BRANCH_APPLY=1`; `build` passes `-var build_sha=…` from `build/manifest.json`); `.env.example`; `.gitignore`; `.gitattributes`; `docs/ai-log/WS0–WS5.md` with a header each. Merge to `main`, tag `g0a-interfaces`, tell James.
2. **`qmc_core` (QMC-101 to QMC-105).** Pure functions, no AWS calls, no I/O except NumPy's RNG:
   - `params`: strict JSON-type validation (booleans are not numbers; `1e6` is not an integer), every error collected, `split_paths`.
   - `seeds`: `parse_qrypt_response`, HKDF-SHA256 (stdlib `hmac`, `hashlib`), `shard_generator` with `SeedSequence(master_int, spawn_key=(i,))` and `PCG64DXSM`. Import NumPy lazily so the API zip never needs it.
   - `sim`, `bs`, `fingerprint`: the exact operation order of HANDOFF §7.3; `math.erf` closed form; the SIMD self-check.
   - `codec`, `aggregate`, `replay`: hex floats, `math.fsum`, Bessel variance, the pure `decide()` used by both finalize paths, `compare()`.
   - `ddb`, `messages`, `errors`, `emf`, `logjson`, `cost` (signature only).
3. **Pin NumPy and the SIMD names (QMC-099, by 2:00 PM, after G0).** Pick the newest NumPy 2.x that has a cp313 `manylinux_2_28_x86_64` wheel; write `requirements/worker.txt` with `--hash` lines (`pip download` + `pip hash`, or `uv pip compile --generate-hashes`). Run in `public.ecr.aws/lambda/python:3.13` (`docker run --platform linux/amd64 --entrypoint python …`): `np.show_runtime()`, and `numpy.lib.introspect.opt_func_info(func_name="exp", signature="float64")` with and without `NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR"`. If NumPy rejects a name, find the valid spelling for that version. Record version, names and the resulting target in HANDOFF §12. (Under emulation on a Mac you may not see AVX-512 at all; that is fine, the point is that the names are accepted and the self-check logic works.)
4. **CI (QMC-106).** `.gitlab-ci.yml` as in the ticket, plus the same jobs in `.github/workflows/ci.yml` (it runs on every mirror push and graders see it, so keep it green). The `golden` job runs in the Lambda Python image with `QMC_ENFORCE_PINNED_DISPATCH=true`, so the golden hash is computed where Lambda computes it; commit `tests/core/test_dispatch_lambda_image.py` so it has a test at G0 (pytest exits 5 when nothing is selected). The guard job fails if `data "aws_ssm_parameter"` appears anywhere in `infra/`. Provide the **IAM allow-list fixture** (QMC-105): a botocore `before-call` hook on the moto sessions that records `(service, operation)` per handler and fails if it is not in HANDOFF §8.3 for that function.
5. **Freeze (QMC-107).** Update HANDOFF with anything you learned, bump its version, push, tag `g0-contract-frozen`, tell James.

## Requirements that are easy to get wrong

- **Bit-exactness is the product.** Follow HANDOFF §7.3 operation by operation: Python floats for `drift`, `vol`, `disc`; `np.exp` on the array; `np.maximum`; per-chunk `np.sum`; `math.fsum` across chunks. No `@`, `np.dot`, `np.mean` or BLAS in the path. Fixed chunk size 131,072.
- `SeedSequence(master_int).spawn(n)` is stateful (a second call continues numbering). Use `spawn_key=(i,)` and prove equality in a test.
- Floats never go to boto3 as `float` or `Decimal(float)`. Shard sums and results are `float.hex()`; parameters are `repr()` strings.
- DynamoDB sets cannot be empty. Builders and readers treat a missing `done_shards`/`failed_shards` as the empty set.
- `mark_failed` must refuse a shard already in `done_shards` (`NOT contains(done_shards, :i)`), and `finalize_complete` must require `attribute_not_exists(failed_shards)`.
- `qmc_core.seeds` must not import `os`, `random`, `secrets` or `time` (a test parses the module's imports). The master seed comes only from the Qrypt block.
- `qmc_core/__init__.py` imports nothing heavy; only `sim` and `fingerprint` import NumPy.
- EMF and JSON logs go to **stdout** with `sys.stdout.write(...)` + flush, never through `logging` (Lambda's handler prefixes the line and CloudWatch then cannot extract the metric). One metric per emission line is fine; `Dimensions: [[]]`; ids as properties, never dimensions.
- Every attribute name in a DynamoDB expression goes through `ExpressionAttributeNames`: `status`, `ttl`, `result`, `size` and `partial` are reserved words.
- `finalize_partial` carries the snapshot condition `size(#done) = :n_done_seen` (or `attribute_not_exists(#done)`), and omits its `ADD` when the failed set is empty.
- `aggregate` with N = 0 returns null and with N = 1 returns null SE/CI: never divide by zero (a job whose every shard crashed must still be reapable).
- The SIMD self-check raises only when `QMC_ENFORCE_PINNED_DISPATCH=true`; elsewhere it warns, so worker tests import anywhere.
- The redaction filter must catch nested keys and header dictionaries, not just top-level keys.

## Done when

- `uv run pytest tests/core --cov=qmc_core` is green with coverage ≥ 90% (100% branch on `params`, `aggregate.decide`, `ddb` condition builders).
- CI is green on `main`; the `golden` job ran in the Lambda image.
- Tags `g0a-interfaces` (9:45 AM) and `g0-contract-frozen` (noon) are on the remote and James has been told; HANDOFF §12 is filled in by 2:00 PM.
- You report: NumPy version, SIMD names and target, coverage, anything in the handoff you had to change, and an AI usage log entry in `docs/ai-log/WS0.md`.

## After G0: integrator

Review and merge each workstream's merge request at the gates (QMC-109). Handle contract changes: read the request in `docs/contract-changes/`, decide with James, change the shared files, bump the HANDOFF version and tell the affected workstreams to rebase. After each merge run `make test`, `make build`, `make apply` (from `main`; you are the only applier after G1, D33) and `make e2e`. At every gate check that GitHub's `main` sha equals GitLab's. You also run the drills that need applies (the fail-closed drill's two applies, `make drift-check`) when WS4 asks, and the G4 "Final sequence" in HANDOFF §2 with WS5. Stretch, if ahead: antithetic variates (QMC-108) and making Checkov blocking (QMC-119).

## Review checklist for James

- [ ] The RFC 5869 vector test exists and passes
- [ ] A test asserts `SeedSequence(m, spawn_key=(i,))` equals `spawn()[i]`
- [ ] `grep -n "import" src/qmc_core/seeds.py` shows no os, random, secrets or time
- [ ] The simulation loop matches HANDOFF §7.3 line for line
- [ ] Builder tests run against moto, including a second finalize being rejected
- [ ] `requirements/worker.txt` has hashes; HANDOFF §12 lists the NumPy version and the SIMD names
- [ ] CI's golden job runs in `public.ecr.aws/lambda/python:3.13` with the enforcement flag and collects at least one test
- [ ] EMF/log lines are written to stdout and a test parses every line as JSON
- [ ] The IAM allow-list fixture exists and fails on an unlisted operation
- [ ] `make apply` refuses from a branch that does not contain `origin/main`
