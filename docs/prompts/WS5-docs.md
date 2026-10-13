# WS5 — Docs and demo

You are building the submission package: the README a grader follows to deploy, run and tear down the platform; the architecture diagram; the cost note; the security note; AI_USAGE.md; the decisions log; and a video of at most five minutes. Docs, AI disclosure and the demo are 10 of the 100 rubric points, and the README is how a grader experiences everything else. You lead G4, the finish line: **Wednesday 2026-10-14, 9:00 PM Central**; 9:00–11:59 PM is buffer.

**Start:** Tue 9:00 AM (skeletons do not need G0). Work on branch `ws5-docs` in `../quantum-seeded-monte-carlo-ws5`. Final text after G3's evidence.

## Read first

1. `docs/ASSIGNMENT.md`: "Submission Requirements", "AI Usage Disclosure", "Security, Privacy & Compliance", "Performance Benchmarks" and the rubric.
2. `docs/PRIMER.md`: your main source for the cost arithmetic, the security reasoning and the replay story.
3. `docs/HANDOFF.md` and `docs/PRD.md`.
4. `docs/DECISIONS.md` (D1–D35 already written; you keep it current).
5. Your tickets in `docs/ROADMAP.md`: QMC-150 to QMC-158.

## You own

`README.md`, `AI_USAGE.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/COST.md`, `docs/SECURITY.md`, `docs/DEMO_SCRIPT.md`, `docs/PRIMER.md`, `docs/images/**`, `docs/ai-log/WS5.md`. Each workstream logs its AI usage in its own `docs/ai-log/WSn.md`; you fold them into `AI_USAGE.md` at G4.

## Build in this order

1. **Skeletons (QMC-150), Tuesday morning.** README with the final headings (below) and placeholders; `docs/ARCHITECTURE.md` with a first Mermaid flowchart from HANDOFF §8; AI_USAGE tools table and log; empty COST, SECURITY and DEMO_SCRIPT with headings.
2. **DECISIONS upkeep (QMC-155)** throughout: D1–D35 are written; add the `kms:Decrypt` outcome, the EMF check outcome, reserved concurrency, pinned versions, any contract change.
3. **README (QMC-151).** In this order: the one-line description (≤ 120 characters, also used for the directory listing); what it does and for whom (analysts, operators, auditors); architecture (link + small diagram); **grader quick-start** (prerequisites; `aws configure`; `aws ssm put-parameter` for the token, with why it is out-of-band; `make build && make apply`; `python clients/qmc.py submit … && wait`; `replay`; reading the result and the Black-Scholes comparison; `make destroy`); calling our deployed endpoint with the grader credentials (`clients/qmc.py`, and `curl --aws-sigv4 "aws:amz:us-east-2:lambda" --user "$AK:$SK"`); API reference (routes, request, errors); how bit-identical replay works and when it would not hold (the fingerprint); guardrails (Budget, alarms, limits, and how the custom metrics relate to Terraform: the namespace is set by Terraform, the alarms and dashboard on the metrics are Terraform resources, the metrics themselves come from EMF log lines so no role needs the `PutMetricData` wildcard); why the worker fails closed at init (the SIMD self-check) and that handlers otherwise never raise; the Qrypt token's expiry date; security summary (link to SECURITY.md); limitations and what we left out (Step Functions, PENDING_ENTROPY, ephemeral environments, VaR, and why); links to COST, BENCHMARKS, TEST_RESULTS, DECISIONS, AI_USAGE and the video.
4. **Architecture (QMC-152).** Every resource in `terraform state list` appears; a flowchart plus sequence diagrams for submit (incl. the fail-closed branch) and for replay. Mermaid renders on GitLab and GitHub; check both.
5. **Cost note (QMC-153).** Recompute PRIMER's table with WS4's measured worker duration and the measured idle polling; state each always-free allowance with its source URL; the rules that keep it at $0; the Budget screenshot (`docs/images/budget.png`).
6. **SECURITY.md and AI_USAGE.md (QMC-154).** SECURITY: the token's path (out-of-band SSM SecureString → runtime fetch → never in state, with the `data "aws_ssm_parameter"` pitfall), the IAM table per function, Function URL auth and the grader user, encryption choices and why no CMKs, logs and redaction, TLS-only queue policies, fault injection left enabled on the graded stack (deliberate, documented), every Checkov suppression with its reason (from WS1). AI_USAGE: tools and models, how AI output was verified (tests, golden hash, e2e, drills), the complete log folded in from `docs/ai-log/WS0–WS5.md`.
7. **Video (QMC-156).** Follow `docs/DEMO_SCRIPT.md` and HANDOFF §2 "Final sequence": record the apply during the rehearsal's apply from empty; run `make warm-dlq` at least 15 minutes before the forced-failure segment (the first DLQ metrics can otherwise take 15 minutes). Segments: `terraform apply` (sped up), a 1M-path job running to COMPLETE, the Black-Scholes comparison in the result, a bit-identical replay, the forced failure → PARTIAL → DLQ alarm → email. At most five minutes; cut the waits.
8. **Fresh-clone rehearsal (QMC-157)** with James, in the HANDOFF §2 "Final sequence": `make destroy` from `main`, clone from GitHub, follow only the README, time it, confirm the SNS email at once, fix what breaks; leave the stack deployed.
9. **Submission (QMC-158).** GitHub repo public and its `main` sha equal to GitLab's; README has the endpoint URL, the one-liner, the video link and the Qrypt token's expiry; the grader access key is created by CLI **last** and sent privately (never committed), and the README says it will be deleted after grading; then no apply or destroy; tick every deliverable.

## Requirements that are easy to get wrong

- **The quick-start must work in someone else's account.** No hard-coded account ids, emails or our state path in commands a grader copies; `terraform.tfvars.example` shows what to fill in.
- **The token never appears** in the README, screenshots, the video or terminal recordings. Use `read -s` in the documented command. Blur the account id in screenshots if James prefers.
- **Every number cites evidence**: BENCHMARKS.md, TEST_RESULTS.md, a REPORT line or a console screenshot. No numbers from memory or from the plan.
- Use the assignment's words for required items (grader quick-start, cost note, architecture diagram, one-line description) so a grader can tick them off.
- Be precise about "bit-identical": same NumPy build, architecture, SIMD target and algorithm version, which every job records; different CPU models are fine and replay ×20 shows it.
- The cost note must say the account is on the Paid plan and why that matters (on the Free plan overage would silently use credits).
- AI_USAGE: specific and honest about what AI produced and what a human verified.

## Done when

- README, ARCHITECTURE, COST, SECURITY, AI_USAGE and DECISIONS are merged on `main` and mirrored to GitHub.
- The video is at most five minutes, covers the five required segments, and is linked from the README.
- The fresh-clone rehearsal passed and WS4 has recorded it.
- You report: the one-liner, video length, rehearsal time, and an AI usage log entry in `docs/ai-log/WS5.md`.

## Review checklist for James

- [ ] One-line description ≤ 120 characters
- [ ] The quick-start has been run end to end from a clean clone
- [ ] Every resource in `terraform state list` is in the diagram
- [ ] COST.md's figures each cite a source or a measurement; Budget screenshot present
- [ ] SECURITY.md explains why the token is created out-of-band and never in state
- [ ] No secret, token or access key in any committed file, screenshot or the video
- [ ] The video shows apply, a completed job, the Black-Scholes comparison, a bit-identical replay and the DLQ alarm firing
