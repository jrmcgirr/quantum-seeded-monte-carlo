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
