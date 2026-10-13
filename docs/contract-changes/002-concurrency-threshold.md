# 002 Reserved-concurrency threshold

- **Requested by:** WS0, 2026-10-13 CT
- **Status:** accepted 2026-10-13 by James; done (HANDOFF 1.7)
- **Affects:** WS0, WS1, WS5; `docs/HANDOFF.md` (§2 steps 0.2 and 0.7, D12, §3, R6), `docs/ROADMAP.md` QMC-090, `scripts/gitlab_issues.csv` QMC-090, `$HOME/.qmc/dev.tfvars`, `infra/` (`terraform.tfvars.example` text), `README.md`

## Need

HANDOFF step 0.2 says to request a Lambda concurrency limit of 1,000 if the current limit is lower. Step 0.7 says to set `worker_reserved_concurrency = 10` only "once the quota is ≥ 1,000". The reason, from D12, is that AWS always keeps at least 100 concurrency unreserved, so a reservation is rejected if it would leave less than that.

The project account (563632884084, us-east-2) already has a limit of 400 (`aws lambda get-account-settings` → `ConcurrentExecutions: 400`, `UnreservedConcurrentExecutions: 400`; Service Quotas `L-B99A9384` = 400, adjustable). Reserving 10 leaves 390 unreserved, well clear of the 100 floor. The 1,000 threshold blocks a setting that already works and makes G1 wait on an AWS quota ticket for nothing.

## Proposed change

The actual rule is that the limit must be at least 100 plus every reservation. With the worker's 10 and the API's none (HANDOFF line 447), that means 110 or more.

| Where | Now | Change to |
| --- | --- | --- |
| HANDOFF §2 step 0.2 | "If under 1,000, request 1,000 in Service Quotas now (reserved concurrency is impossible below the 100-unreserved floor, D12)" | "If under 110, request 1,000 in Service Quotas now (a reservation must leave at least 100 unreserved, D12)" |
| HANDOFF §2 step 0.7 | "`worker_reserved_concurrency = 10` once the quota is ≥ 1,000" | "`worker_reserved_concurrency = 10` once the limit is ≥ 110" |
| HANDOFF D12, §3, R6 | "once the Day 0 quota increase lands" and similar wording | "once the limit is ≥ 110 (100 unreserved + 10)" |
| ROADMAP QMC-090, `scripts/gitlab_issues.csv` QMC-090 | "an increase to 1,000 requested" | "limit checked; an increase to 1,000 requested only if under 110" |
| `terraform.tfvars.example` (WS1), README (WS5) | how to check the limit | the same check, with 110 as the threshold |

The Terraform variable, its `null` default (R22) and `maximum_concurrency = 5` all stay as they are.

## Alternatives considered

- **Request 1,000 anyway.** It is free and usually approved, but G1 would hang on an AWS ticket, and the extra headroom buys nothing because `maximum_concurrency` caps fan-out at 5. A later request is still possible if a drill needs it.
- **Leave `worker_reserved_concurrency` null.** Fan-out is still capped at 5, but the worker loses the hard upper bound that §10 (cost guardrails) lists against cost runaway. Worse.

## Impact on invariants

- **I7:** strengthened. The 10-unit reservation (the hard cost bound) is in force from the first apply instead of after a quota ticket.
- **I1 to I6:** unchanged. No result bits change, so no new `algo_version`.
