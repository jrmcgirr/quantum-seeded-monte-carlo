# AI usage

WS5 completes this file before submission, folding in each workstream's log from `docs/ai-log/WSn.md` (each workstream appends to its own file at the end of every working session).

## Tools

| Tool | Model | Used for |
| --- | --- | --- |
| Claude (claude.ai, linked to James's computer) | Claude Opus 5.5 (`claude-opus-5-5`) | Research (five research sub-agents and a report writer), decision walk-through, PRD, handoff, roadmap, workstream prompts, GitLab plan, independent plan review |
| Claude Code | Claude Opus 5.5 (`claude-opus-5-5`) for WS0 and WS5 so far; *(other workstreams at G4)* | Workstream implementation, Day 0 account setup, docs |

## How AI claims were verified

*(WS5: describe the verification approach — committed closed-form, seed and golden-hash tests; moto integration tests for duplicates and PARTIAL; `make e2e` on Lambda; replay ×20; the deployed drills; `make drift-check`; benchmarks — and any claim that turned out wrong.)*

## Log

| When (CT) | Workstream | Tool and model | Asked | Produced | Human check or change |
| --- | --- | --- | --- | --- | --- |
| 2026-10-12 20:08–20:55 | Planning | Claude, Opus 5.5, with five research sub-agents (Qrypt API; AWS free tier and Function URLs; fan-out/fan-in patterns; Monte Carlo numerics and reproducibility; Terraform, security and CI) and a report-writer sub-agent | Research the platform end to end, walk through the decisions, then write the PRD, handoff, roadmap, decisions, workstream prompts and GitLab plan in the format of the telemetry project | `docs/PRIMER.md` (the research report, also saved to the claude.ai project), `docs/PRD.md`, `docs/HANDOFF.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/prompts/WS0–WS5`, `scripts/gitlab_issues.csv`, `scripts/create_gitlab_issues.py`, `scripts/bootstrap.sh`, `CLAUDE.md` | James chose Python 3.13 + NumPy, x86_64 with pinned SIMD, `AWS_IAM` auth, GitLab primary with GitLab CI, all four stretch goals and the Tue–Wed timeline, and accepted the other defaults. Research findings that changed the plan: NumPy's AVX-512 `exp` differs by 1 ULP (local experiment by a research sub-agent, NumPy 2.5.3), SQS idle polling ≈ 648k requests/month (re:Post, unverified in AWS docs), rate-limit cap must be 15 not 25–30 (arithmetic). Not yet verified live: Qrypt latency, quota and signup; EMF with an empty dimension set; whether `kms:Decrypt` is needed for `aws/ssm` |
| 2026-10-12 20:55–21:30 | Planning | Claude, Opus 5.5, independent review sub-agent (had not seen the drafting) | Adversarially review the whole package against the assignment and the research | 6 high, 16 medium, 19 low findings: applies from the wrong worktree redeploy stale code; G0 over-serialized; the SIMD self-check breaks CI on AVX-512 runners; a reaper race can mark a completed shard failed; EMF through `logging` never publishes; the GitHub mirror stops on workflow changes without the workflow scope; DLQ metrics can lag 15 min; reserved words; divide-by-zero; and others | All high and medium findings and most low ones applied to HANDOFF (v1.1), ROADMAP (QMC-099 added, 50 tickets), DECISIONS (D33–D35), prompts and CLAUDE.md. *(James to review before bootstrap)* |
