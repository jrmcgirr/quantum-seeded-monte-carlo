# WS0 AI usage log

## 2026-10-13 05:00–05:32 CT

- **Workstream:** WS0 (Day 0, QMC-090)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`)
- **Asked:**
  - Set up the AWS Agent Toolkit for the `qmc` profile (us-east-2, new AWS experience).
  - Explain the single-Region rule and decide whether to move to us-east-1.
  - Draft, accept and apply contract change 001 (Region us-east-2).
  - Advise on the Lambda concurrency increase and draft 002.
  - Commit the WS0 work and merge it to `main`.
- **Produced:**
  - AWS CLI 2.37.13 installed (`~/.local/bin`) and the `qmc` profile set to us-east-2.
  - `docs/contract-changes/001-region-us-east-2.md` (accepted) and `002-concurrency-threshold.md` (proposed).
  - us-east-1 changed to us-east-2 in HANDOFF (bumped to 1.2), ROADMAP, the WS1 and WS5 prompts and `scripts/gitlab_issues.csv`.
  - Commit `cae4581` on `ws0-foundation`, merged to `main`.
- **Checks run:**
  - `aws sts get-caller-identity` (account 563632884084).
  - `aws freetier get-account-plan-state`: `FREE`, then `PAID` after the upgrade.
  - `aws lambda get-account-settings` and Service Quotas `L-B99A9384`: concurrency limit 400, adjustable.
  - `grep` finds no `us-east-1` left in WS0 files.
  - Secret-pattern scan of the staged files: no hits.
- **Human checked or changed:**
  - James did the browser sign-in (`aws login`).
  - James ran the Agent Toolkit wizard and appended the AWS rules to `CLAUDE.md` (auto mode blocked the agent from doing either).
  - James confirmed the project Region (us-east-2) in AWS Settings.
  - James upgraded the account to the Paid plan (D25).
  - James chose us-east-2 over a new us-east-1 account, accepted 001, and chose to commit on `ws0-foundation` before bootstrap.
- **Open:**
  - 002 awaits a decision.
  - `docs/DECISIONS.md` D24 needs updating by WS5.
  - The Qrypt latency samples should be taken from us-east-2.

## 2026-10-13 05:36–06:05 CT

- **Workstream:** WS0 (QMC-100 to QMC-107, QMC-099)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`)
- **Asked:**
  - Execute `docs/prompts/WS0-foundation.md`.
  - Send WS1–WS5 their assignments.
- **Produced:**
  - G0a (`g0a-interfaces`, main 7395b1a): pyproject/uv, the final Makefile with the D33 guard, stub handlers, `qmc_core` interface stubs, `tests/conftest.py` (moto fixtures and the IAM allow-list guard), and HANDOFF 1.3 §4.1.
  - G0 (`g0-contract-frozen`, main 8222b9b): `qmc_core` implemented. 208 tests pass and `qmc_core` has 100% line and branch coverage (`make test`).
  - `.gitlab-ci.yml` and `.github/workflows/ci.yml`.
  - NumPy 2.5.4 hash pin, with HANDOFF 1.4 §12 filled in.
  - Answered WS2/WS3/WS4 interface questions. Accepted additions:
    - `qrypt_call` log event
    - `set_entropy_source`
    - `ALGO_VERSION`/`CHUNK_SIZE` in `params`
    - `clients.qmc.request`
- **Checks run:**
  - `make lint`, `make test`.
  - `make test-golden` in `public.ecr.aws/lambda/python:3.13` under Docker amd64 emulation: 5 passed.
  - NumPy probe: `np.show_runtime()`, `opt_func_info` with and without the disable names.
    - Unknown names are silently ignored.
    - `X86_V3` disable proves the mechanism.
    - `exp` → `X86_V3`.
  - Guard: refuses a checkout at 96f0f25; `ALLOW_BRANCH_APPLY=1` overrides.
  - All §6.2 builders were exercised against moto.
- **Human checked or changed:**
  - James allowed the merge to `main` and the tags after auto mode blocked them.
  - Auto mode also blocked `make apply`. It was not needed; the guard was tested via `make guard-main`.
- **Open:**
  - GitLab has no runner. Pipeline #52654 is stuck in pending.
  - No GitHub push mirror is configured (`remote_mirrors` is empty), so neither CI system has run. CI green is not yet proven; the golden constants from emulation are provisional until an x86 CI run.
  - Contract change 002 is still proposed.
