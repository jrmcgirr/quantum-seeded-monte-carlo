# Quantum-Seeded Monte Carlo

Serverless Monte Carlo option pricing on AWS, seeded from quantum entropy, with bit-for-bit replay at $0/month.

> Status: planning complete, build starting Tuesday 2026-10-13. WS5 replaces this README with the grader quick-start and full instructions at gate G4.

## Planned stack

`POST /jobs` (Lambda Function URL, `AWS_IAM`) → Qrypt entropy → HKDF master seed → DynamoDB job record → SQS (one message per shard) → worker Lambdas (NumPy, `SeedSequence` spawn keys, `PCG64DXSM`) → DynamoDB shard results → fan-in → estimate, standard error, 95% CI and the Black-Scholes comparison. `POST /jobs/{id}/replay` re-runs from the stored seed and proves the result is bit-identical. Guardrails: a $1 AWS Budget, eight custom metrics, seven alarms (including DLQ depth > 0), all in Terraform, all in the always-free tier.

## Documents

| Document | What it is |
| --- | --- |
| [docs/PRIMER.md](docs/PRIMER.md) | Research report: reproducibility, Qrypt, fan-in patterns, the free-tier arithmetic, Terraform and IAM, risks, the 21 decisions |
| [docs/PRD.md](docs/PRD.md) | Product requirements and success metrics |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Engineering contract: the single source of truth for every workstream |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Gates, workstreams and the ticket board (mirrors GitLab) |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decisions D1–D35 with alternatives and reasons |
| [docs/prompts/](docs/prompts/) | Session prompts for WS0–WS5 |
| [docs/ASSIGNMENT.md](docs/ASSIGNMENT.md) | The assignment |
