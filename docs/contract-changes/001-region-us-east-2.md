# 001 Region us-east-2

- **Requested by:** WS0, 2026-10-13 05:21 CT
- **Status:** accepted 2026-10-13 (HANDOFF 1.2, branch `ws0-foundation`)
- **Affects:** WS0, WS1, WS2, WS4, WS5; `docs/HANDOFF.md`, `docs/ROADMAP.md`, `docs/prompts/WS1-infra.md`, `docs/prompts/WS5-docs.md`, `scripts/gitlab_issues.csv`, `.env.example`, `infra/` (`region` variable), `README.md`, `docs/DECISIONS.md`, `docs/COST.md`

## Need

The project AWS account (563632884084) uses the new AWS experience. That experience pins a project to one Region, chosen from the contact address, and enforces the pin with managed SCPs and RCPs. This project's Region is **us-east-2** (US East, Ohio), confirmed in AWS Settings > Additional Info. Regional resources (Lambda, SQS, DynamoDB, SSM, CloudWatch alarms) cannot be created anywhere else, so a `make apply` in us-east-1 as D24 specifies would be denied. Nothing is deployed yet (`infra/` does not exist), so the move costs only text and defaults.

## Proposed change

Change every us-east-1 to us-east-2:

| Where | Now | Change to |
| --- | --- | --- |
| HANDOFF §2 step 0.1 check (line 43) | `AWS_REGION=us-east-1` | `AWS_REGION=us-east-2` |
| HANDOFF G1 (line 58) | "`make apply` from empty succeeds in us-east-1" | "… in us-east-2" |
| HANDOFF D24 (line 100) | Region **us-east-1** (Qrypt's only documented endpoint is `api-eus`, East US). | Region **us-east-2**: the account's new-AWS-experience project is pinned there (contract change 001); Ohio is also near Qrypt's `api-eus` (East US). |
| HANDOFF cost estimate (line 417) | "Priced at us-east-1 list prices" | "Priced at us-east-2 list prices" (WS4: check each constant and source URL against the us-east-2 price list) |
| HANDOFF §8.4 Terraform variables (line 504) | `region` (`us-east-1`) | `region` (`us-east-2`) |
| ROADMAP QMC-090 (line 66) and `scripts/gitlab_issues.csv` QMC-090 | "a CLI identity in us-east-1" | "… in us-east-2" |
| ROADMAP G0a (line 72), `.env.example` | `AWS_REGION=us-east-1` | `AWS_REGION=us-east-2` |
| ROADMAP QMC-114 (line 182) and `scripts/gitlab_issues.csv` QMC-114 | "First apply from empty in us-east-1" | "… in us-east-2" |
| `docs/prompts/WS1-infra.md` (line 43) | "succeeds in us-east-1" | "succeeds in us-east-2" |
| ROADMAP QMC-151 (line 326), `docs/prompts/WS5-docs.md` (line 23), `scripts/gitlab_issues.csv` QMC-151 | `curl --aws-sigv4 "aws:amz:us-east-1:lambda"` | `curl --aws-sigv4 "aws:amz:us-east-2:lambda"` |
| `docs/DECISIONS.md` D24 (WS5) | "Chose: us-east-1" | Record us-east-2, the account pin as the reason, and us-east-1 as the rejected alternative |

These stay as they are: `QRYPT_URL` and `seed_provenance.endpoint` (`api-eus` is Qrypt's endpoint, not an AWS Region), and the Toolkit's `--region us-east-1`, which is a global endpoint and creates no project resources.

No API field, DynamoDB attribute, SQS message field or `qmc_core` interface changes. `clients/qmc.py` already reads the Region from the URL (HANDOFF line 267).

## Alternatives considered

- **Keep us-east-1 and use a standard-sign-up account.** This means a second account, a new CLI identity and a new Qrypt token stored in SSM there. That is more setup and more risk than editing text when nothing is deployed yet. Rejected.
- **Turn on advanced features for this project to lift the Region pin.** It is not confirmed that this works, and it changes the account's managed guardrails. Rejected unless us-east-2 turns out to be unusable.
- **Move the project to us-east-1.** We know of no supported way to change a project's Region. Rejected.

## Impact on invariants

- **I1, I2, I3, I4, I6:** unchanged. A Region is not part of any seed, key, message or secret path. The SSM parameter ARN is already built from `${region}` (HANDOFF line 438), so the token has to be stored in us-east-2 (`aws ssm put-parameter --region us-east-2`).
- **I5:** unchanged. Lambda x86_64 runs the same `public.ecr.aws/lambda/python:3.13` base in both Regions, and the golden hashes do not depend on the Region. G1 still requires the SIMD self-check to pass on the deployed function.
- **I7:** unchanged. The always-free allowances for Lambda, SQS, DynamoDB provisioned capacity (25/25 per account per Region), CloudWatch, and SSM standard parameters apply in us-east-2 as well. Budgets are global.
- **Bits:** no change to simulation or aggregation, so no new `algo_version`.

## Open items (not part of this change)

- **D25:** `aws freetier get-account-plan-state` reports `accountPlanType: FREE` with $100 in credits, ending 2027-04-13. D25 requires the Paid plan, and R15 explains why: credits would hide any overage. Upgrade in AWS Settings > Billing before G1.
- **Qrypt latency:** take the 10 latency samples from step 0.1 from us-east-2 once the API Lambda is deployed, and record them in §12.
