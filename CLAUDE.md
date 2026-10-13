# Quantum-Seeded Monte Carlo

Serverless Monte Carlo pricing of a European call on AWS: a Lambda Function URL (`AWS_IAM`) takes a job, seeds it from Qrypt quantum entropy, fans it out over SQS to worker Lambdas with derived shard seeds, aggregates in DynamoDB, and replays any job bit for bit. Everything is Terraform; everything stays in the AWS always-free tier.

`docs/HANDOFF.md` is the single source of truth. Read it fully before writing code; it wins over `docs/ASSIGNMENT.md`. `docs/ROADMAP.md` has the gates and tickets; your session prompt is in `docs/prompts/`.

## The invariants (never break these)

1. **I1** Seeded only from Qrypt. Any Qrypt or secret failure rejects the job, naming the cause, and writes nothing. No local, time-based or reused seed, ever.
2. **I2** Shard *i*'s stream depends only on `(master seed, i)`: `SeedSequence(master_int, spawn_key=(i,))` → `PCG64DXSM`.
3. **I3** Each shard is counted at most once: conditional put on `(JOB#id, SHARD#iiii)` + `ADD done_shards` to a number set.
4. **I4** COMPLETE only when all shards are present and none failed; otherwise PARTIAL with the failed indexes. Every status write is conditional on `status = QUEUED`.
5. **I5** Bit-identical replay: pinned NumPy, x86_64, `NPY_DISABLE_CPU_FEATURES` + fail-closed SIMD self-check, fixed chunk size, `math.fsum`, hex-float storage, fingerprint per shard.
6. **I6** The Qrypt token is never in Terraform state, the repo or the logs. Never `data "aws_ssm_parameter"`.
7. **I7** $0: one SQS event source mapping, provisioned DynamoDB, ≤ 10 metrics and alarms, no customer-managed keys, 14-day logs.

## Commands

| Command | What it does |
| --- | --- |
| `make setup` | `uv sync` (Python 3.13) |
| `make test` | Unit and integration tests (moto, fake entropy; no AWS, no token) |
| `make test-golden` | Golden-hash tests inside `public.ecr.aws/lambda/python:3.13` (Docker) |
| `make lint` / `make fmt` | ruff, `terraform fmt`, tflint |
| `make build` | Deterministic `build/api.zip` and `build/worker.zip` |
| `make plan` / `make apply` / `make destroy` | Terraform with state at `$HOME/.qmc/state/$(ENV).tfstate` and settings at `$HOME/.qmc/$(ENV).tfvars`; apply/destroy refuse a checkout without `origin/main` (WS1 until G1, then the integrator from `main` only) |
| `make outputs` | Print the deployed URL and names |
| `make e2e` (`--fresh` right after an apply from empty) | 1M-path reference job → progress → COMPLETE → Black-Scholes check → replay → bit-identical |
| `make warm-dlq` / `make force-failure` / `make burst` / `make replay-check N=20` / `make drift-check` / `make bench` | WS4's drills and benchmarks (warm the DLQ ≥ 15 min before a forced failure; the integrator runs drift-check) |
| `python clients/qmc.py <submit/get/wait/replay/list/health>` | SigV4 client for the Function URL |

## Layout and ownership

| Workstream | Owns (only these paths may be edited) |
| --- | --- |
| WS0 Foundation and contract | Root files except `README.md` and `AI_USAGE.md` (incl. Makefile, `.gitattributes`), `src/qmc_core/**`, `tests/core/**`, `requirements/**`, `.gitlab-ci.yml`, `.github/workflows/**`, `scripts/bootstrap.sh`, `scripts/*gitlab*`, `scripts/issue_map.csv`, `docs/HANDOFF.md`, `docs/ROADMAP.md`, `docs/prompts/**`, `docs/contract-changes/**`, `CLAUDE.md` |
| WS1 Infrastructure | `infra/**` (incl. `infra/.tflint.hcl`), `scripts/build_lambdas.py` |
| WS2 API | `src/qmc_api/**`, `tests/api/**`, `clients/**` |
| WS3 Worker | `src/qmc_worker/**`, `tests/worker/**` |
| WS4 Verification | `tests/correctness/**`, `tests/integration/**`, `tests/deployed/**`, `tools/**`, `docs/TEST_RESULTS.md`, `docs/BENCHMARKS.md` |
| WS5 Docs and demo | `README.md`, `AI_USAGE.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/COST.md`, `docs/SECURITY.md`, `docs/DEMO_SCRIPT.md`, `docs/PRIMER.md`, `docs/images/**` |

- **Interface changes go through WS0 only.** A DynamoDB attribute or key, an SQS message field, an API field or error code, an environment variable, a Terraform output another workstream reads, or any `qmc_core` change: write `docs/contract-changes/NNN-short-title.md` (see the README there) and tell James.
- Each workstream also owns its AI usage log `docs/ai-log/WSn.md`; WS5 folds them into `AI_USAGE.md`.
- Only WS1 (until G1, rebased on `main`, announced) and the integrator (from `main`) run `make apply` / `make destroy`, one at a time. Never change Lambda or other AWS configuration by CLI: it causes drift.

## Using qmc_core

```python
from qmc_core.params import validate_job_request, split_paths    # strict request validation, path split
from qmc_core.seeds import parse_qrypt_response, derive_master_seed, shard_generator
from qmc_core.sim import simulate_shard                         # imports NumPy: worker only
from qmc_core.fingerprint import fingerprint, assert_reproducible_dispatch   # NumPy: worker only
from qmc_core.bs import black_scholes_call
from qmc_core.aggregate import aggregate, decide                # the only result computation; finalize decisions
from qmc_core.replay import compare
from qmc_core.codec import to_hex, from_hex
from qmc_core import ddb, messages, errors, emf, logjson        # write builders, SQS v1, error codes, EMF, JSON logs
```

Every rule has exactly one implementation, in `qmc_core`. Handlers are thin. Never re-implement validation, seeding, aggregation or a DynamoDB expression elsewhere.

## Conventions

- **Versions:** Python 3.13; NumPy pinned in `requirements/worker.txt` (hashes); Terraform ≥ 1.10; AWS provider `~> 6.0`. If a pinned version will not install, stop and report. Do not downgrade silently.
- **Floats:** never round; never send a Python float to boto3; store `float.hex()` (results) or `repr()` (parameters); compare results by hex string only.
- **No NumPy in the API** (`qmc_core.sim` and `qmc_core.fingerprint` are the only NumPy importers).
- **Logs and metrics go to stdout as single-line JSON** via `qmc_core.logjson` / `qmc_core.emf`, never through `logging` (Lambda's handler prefixes lines and breaks EMF).
- **DynamoDB expressions:** only through `qmc_core.ddb` builders; every attribute name via `ExpressionAttributeNames` (`status`, `ttl`, `result`, `size` are reserved words). Tests use WS0's IAM allow-list fixture.
- **The worker's SIMD self-check** enforces only with `QMC_ENFORCE_PINNED_DISPATCH=true` (Lambda, the golden CI job); elsewhere it warns.
- **Config:** every limit is an environment variable set by Terraform with the HANDOFF §8.4 default, logged once at init.
- **Secrets:** none in the repo, tfvars, fixtures or logs. Tests use `FakeEntropySource`; no test needs a token.
- **Qrypt budget:** every live submit spends 1 KiB of a finite, unpublished monthly quota. Never loop live submits without a cap. Poll job status at 2 s, not faster (10 RCU).
- **Stay in your lane.** Edit only the paths your workstream owns.
- **Tests are part of done.** A ticket is done when its tests pass from a clean checkout.
- **Git:** one branch per workstream (`ws1-infra`, …), small commits prefixed `WSn:` with the ticket id, merge request **into `main`** with `Closes #<issue>`. `main` is protected: no direct pushes, merge requests only, the pipeline must pass (CI runs only on merge requests). The integrator merges and applies from `main`.
- **Verify, don't assert.** Every claim in a report or doc cites a test, a command output, a log line or a screenshot.
- **AI usage log:** at the end of each session append to `docs/ai-log/WSn.md`: time, workstream, tool and model, what was asked, what was produced, what a human checked or changed.
- **Ask, do not guess,** when the handoff is silent on something that affects another workstream.
- **Record decisions** in `docs/DECISIONS.md` (via WS5): what we chose, the alternatives, why.

<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance for the new AWS experience

This user has signed up for the new AWS experience. This experience lets you sign into AWS using a social provider and requires the following additional context.

Where this guidance conflicts with the project's own instructions, the project's instructions take precedence.

## Context

### Terminology:

- Say "project" instead of "account" — a project contains an AWS account and settings for sharing with other collaborators
- Say "team member" instead of "IAM user" — users are invited by email, not created or federated in IAM
- Say "AWS Settings" when referring to management tasks at [settings.aws.com](https://settings.aws.com/) (project management, billing, team members, spend limits). Users view their actual AWS resources in the AWS Management Console.
- Say "selected Region" when referring to the user's Region — not "home Region"
- The user has a managed IAM experience. This includes a managed service control policies (SCP) and resource control policies (RCP) that govern the use of AWS. They will still need to use IAM to create policies to let services work with each other. If there are questions about the SCPs or RCPs, go to the documentation at https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html

### Constraints:

- All projects share a single AWS Region determined by the user's contact address. Resources cannot be created in other Regions
- When developing:
  - MUST create all Regional resources in the project's assigned Region
  - You CAN create AWS WAF and Cloudwatch Logs resources in us-east-1 when there are global resources (like a global WAF instance) that require a connection to dependencies in us-east-1. You should not use these for any other reason, because resources in the selected Region will provide lower cost (due to no cross-Region traffic), increased availability (due to no cross-Region traffic), and easier manageability (due to not needing to look in another Region). When you need to do an inventory of resources, you need to look in both the selected Region and us-east-1 for Cloudwatch Logs or WAF resources.
  - MUST NOT attempt to create Lambda, API Gateway, or other Regional resources in any other Region
  - MUST direct users to confirm their Region in AWS Settings > View all projects > Overview > Additional Info > Region. If the user cannot confirm their Region, check in ~/.aws/config
  - MUST NOT use Lambda@Edge — excluded from both Lambda and CloudFront
  - MUST NOT use CloudFormation StackSets — no multi-account or multi-Region deployments
  - MUST NOT attempt cross-Region actions — no cross-Region replication for DynamoDB/S3/RDS, no multi-Region KMS keys
  - MUST NOT use Route 53 cross-Region routing — geolocation, latency-based, and failover routing policies are not available
  - CloudFront is a global service and its actions ARE allowed in `us-east-1`. A user can create a CloudFront distribution pointing to their project-region Lambda function URL or API Gateway. However, Lambda and API Gateway themselves MUST NOT be created in `us-east-1` — they must be in the project Region.
  - Reduced availability in `eu-north-1` specifically: Amazon Rekognition, Amazon Textract, Amazon Personalize, AWS App Runner are not available in that Region.
- IAM permissions for human access are managed by AWS. Don't assign roles to team members unless absolutely necessary
- The user may have a spend limit if they are on the paid plan. The limit that pauses their project if it's exceeded. If resources suddenly become inaccessible, ask if they have a spend limit configured. Only project owners can modify a spend limit.
- When developing:
  - MUST ask about spend limit status if the user reports sudden "Access Denied" errors on operations that previously worked
  - MUST direct users to check spend status in AWS Settings > Billing
  - MUST check if a user has upgraded their account to the paid plan
  - MUST ask the user if they want to clean up the successfully created resources or keep them to reduce cost
- The user sets up billing, creates spend limits, and retrieves and pays invoices in AWS Settings. The user creates budgets and optimizes their costs in the AWS Billing and Cost Management console
- Not all AWS services are available. If a service isn't working, do the following:
  1. Run the command `aws freetier get-account-plan-state`
  2. If accountPlanType": "FREE", check the [Free Tier supported services list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#supported-services-free-tier) next,
  3. If accountPlanType": "PAID", check the [Paid Tier supported services list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#supported-services-paid-plan).
  4. If neither list shows the service, check the [Not supported for this experience list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#unsupported-services). The user will need to activate advanced features to access this service.
- Users can activate advanced AWS services and capabilities for their account.
- Before starting a task, check whether a relevant AWS skill is available. Load the skill with retrieve_skill and prefer its guidance over general knowledge.

### Help level

- help_level (required): LOW, MEDIUM, or HIGH. While a user is building, you MUST ask the user: "How much guidance would you like from me? Low (I only flag security risks), medium (I ask a couple of clarifying questions if something seems off), or high (I explain what I'm doing, suggest alternatives, and flag best practices)."

You CAN update this rule file to save a user's help_level.

Constraints for each level:

**LOW:**

- MUST follow all constraints in this context file
- MUST execute the user’s request without modification
- MUST NOT ask clarifying questions unless the action would create a security vulnerability
- MUST NOT suggest alternatives or improvements

**MEDIUM:**

- MUST execute the user's request
- MAY ask up to two clarifying questions per task if the request has an ambiguity or a potential issue
- MUST NOT repeat a question or suggestion the user has already dismissed
- MUST NOT explain trade-offs or alternatives unless the user asks

**HIGH:**

- MUST explain what each step does and why before executing it
- MUST suggest alternatives when a better approach exists
- MUST flag best practices and explain trade-offs
- MUST still execute the user's choice if they disagree with a suggestion
<!-- END AWS Agent Toolkit rules -->
