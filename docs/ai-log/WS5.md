# WS5 AI usage log

## 2026-10-13 morning CT

- **Workstream:** WS5 (QMC-150, QMC-155)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`); assignment relayed from the WS0 session
- **Asked:** Start the WS5 prompt with the skeletons, before G0.
- **Produced:**
  - `README.md` with the final headings in the prompt's order, the one-line description draft, the quick-start commands from HANDOFF (marked *TBD* until the rehearsal) and a small Mermaid diagram.
  - `docs/ARCHITECTURE.md`: a resource flowchart from HANDOFF §8 and sequence diagrams for submit (with the fail-closed branch) and replay.
  - Heading-only `docs/COST.md`, `docs/SECURITY.md` and `docs/DEMO_SCRIPT.md`.
  - `docs/DECISIONS.md` D24 rewritten for us-east-2 (contract change 001). Contract change 002 is still proposed and not recorded as decided.
  - `AI_USAGE.md` tools row for Claude Code.
- **Checks run:** `npx @mermaid-js/mermaid-cli` rendered all four Mermaid blocks with no errors. GitLab and GitHub rendering not checked yet.
- **Human checked or changed:** *(James to review)*

## 2026-10-13, rest of the day (CT)

- **Workstream:** WS5 (QMC-150, 151, 152, 153, 154, 155, 129)
- **Tool and model:** Claude Code, Claude Opus 5.5 (`claude-opus-5-5`). Instructions came from the WS0 integrator session and from James.
- **Asked:**
  - Keep DECISIONS current with every ruling and contract change.
  - Write SECURITY.md and COST.md from evidence.
  - Add measured results to the README.
  - Render the dashboard; commit James's Budget and DLQ-email screenshots.
  - Record the parked stretch items.
- **Produced:**
  - **DECISIONS:** D10 rewritten through contract changes 004–006 (Qrypt limiter 15/10 s → 10/10 s → 1/1 s → 2 s gap). D19 (GitHub as a second remote), D21 amended, D24 (us-east-2), D36, and D37–D46.
  - **SECURITY.md:** complete except a log scan.
  - **COST.md:** $0 at 100 jobs/day, from measured CloudWatch data. Lambda compute is 3.4% of the free allowance measured, 23% in the worst case; SQS is 75%, the binding limit.
  - **README:**
    - Results: the G1 result table, replay ×20, the burst table across four limiters, and measured performance.
    - Guide: Limitations and the grader-key teardown note.
    - Images: the dashboard images (`docs/images/render_dashboard.py`), and James's `budget.png` and `dlq-alarm-email.png`.
  - **Merge requests:** !17, !23, !25, !26, !30, !31, !36.
- **Checks run (every cited figure was re-checked at its source):**
  - **Read-only AWS calls:**
    - IAM: the deployed policies on all four principals match the Terraform statement for statement, and the grader had 0 keys. Re-checked after the fail-closed drill was reverted.
    - Lambda: `GetFunctionConfiguration` on the API (fault injection on, cache TTL 300 s).
    - Budgets: `DescribeSubscribersForNotification` shows one SNS subscriber per alert, and month-to-date spend is $0.0.
    - CloudWatch: `GetMetricStatistics` for the cost model.
    - DynamoDB: `DescribeTable`.
  - **Checkov 3.3.28:** run locally three times (137/0/20). That gives **13** distinct skipped checks, not the 14 WS1 reported.
  - **CI runs:** GitLab pipeline 52736 and GitHub Actions run 38392842904, through their APIs.
  - **Clone:** an anonymous clone of the GitHub repo (it was private at first; reported, then fixed).
  - **AWS docs:** the SCP statements `DenyIAMRestrictedActions` (`iam:*LoginProfile*`, `iam:*Provider*`, groups) and `DenyNotificationContactsOperations`.
  - **Other workstreams' results:** WS4's figures against TEST_RESULTS and BENCHMARKS. Found that the 13:13Z burst's "max 1 Qrypt call" was 2 (a tool early-stop bug); WS4 corrected it.
  - **Rendering:** Mermaid with mermaid-cli.
- **Mistakes made and fixed:**
  - Twice I wrote a reason no one had recorded (D10's 1 s windows, D45's VaR). Both were removed before merge.
  - One push landed after its MR had auto-merged (!23), so it had to be re-sent as !25.
  - The dashboard render called the billed `GetMetricWidgetImage`, about $0.0006. It is disclosed in COST.md, and the script is marked not to be re-run.
- **Human checked or changed:**
  - James took the Budget and DLQ-email screenshots.
  - James made the rulings recorded as D10 (004–006), D21, D40, D42, D43 and D45/D46.
  - WS0 reviewed and merged every MR.
  - James confirmed the Mermaid diagrams render in GitLab's viewer (QMC-150 acceptance). WS5 checked the public GitHub copy of `docs/ARCHITECTURE.md` in a headless browser (Playwright): all three diagrams render as Mermaid viewer frames, including the replay sequence once scrolled into view.

## 2026-10-13 12:55–15:25 CT, WS5 Docs (QMC-126/127/128/108 docs, QMC-151, 153, 154, 156)

- **Tool and model:** Claude Code, Opus 5.5, with one general-purpose sub-agent that folded `docs/ai-log/WS0–WS5.md` into `AI_USAGE.md`. Tasks came from the WS0 integrator session.
- **Asked:** document the stretch features as they landed (cost attribution, Step Functions watcher, PENDING_ENTROPY, antithetic), then the G4 docs: README quick-start and API reference, the remaining COST and SECURITY figures, DEMO_SCRIPT, AI_USAGE.
- **Produced:**
  - MRs !49 (cost cross-check), !53 (007/008 docs: Checkov 140/0/22, IAM re-check, diagrams, cost, README, D21), !59 (G3 stretch evidence), !64 (README quick-start, API reference, guardrails, security summary; SECURITY log scan; COST idle, storage, Budget; DEMO_SCRIPT), and this AI_USAGE consolidation.
- **How it was verified:**
  - Checkov re-run myself on MR !50's head and on `main` `581563d`.
  - Read-only AWS calls only: `GetFunctionConfiguration`, `GetFunctionEventInvokeConfig`, `ListStateMachines`, `GetRolePolicy`, `GetFunctionUrlConfig`, `DescribeAlarms`, `GetFunctionConcurrency`, `ListEventSourceMappings`, `GetMetricStatistics`, `DescribeTable`, `DescribeBudget`; the Price List API for Step Functions' us-east-2 price ($0.000025 a transition). Two signed `GET /jobs/{id}` calls for the `cost_estimate` example (no Qrypt).
  - Every drill figure checked against TEST_RESULTS at `8e0eac5`; the API reference against `qmc_core.errors`, `params`, the handler and the deployed limits; the seed chain against `seeds.py`.
  - Mermaid re-rendered with mermaid-cli after each diagram change.
  - Spot-checked the sub-agent's AI_USAGE figures against TEST_RESULTS.
- **Mistakes made and fixed:** !53 first said the PENDING_ENTROPY backoff was 2, 4, 8 s, read from the environment variable only; the code caps each wait to fit the 10 s timeout, so it is 2, 4, 6, 6 s. WS0 pointed it out; fixed in !53 before merge.
- **Found for others:** HANDOFF still said "nothing is written" (I1), "no Step Functions" (I7, §10) and "No PENDING_ENTROPY queue" (D10) after 007/008, fixed by WS0 in 1.18; HANDOFF §5.3's 429 and `QRYPT_RATE_LIMITED` rows still describe pre-006 behaviour (reported to WS0).
- **Human checked or changed:** WS0 reviewed and merged every MR. *Pending: the fresh-clone rehearsal with James, the Qrypt token's expiry date and the video.*

## 2026-10-15, final day: rehearsal evidence into the docs (WS5)

- **Tool and model:** Claude Code, Opus 5.5. Instructions from the WS0 session and James.
- **Asked:** fold the fresh-clone rehearsal (QMC-157) into the docs; update the README and DECISIONS for contract change 010; record the watcher as on in the deployed stack; switch the demo's segment 5 to the saved alarm-email screenshot (James).
- **Produced:**
  - README: the rehearsal note, the guard-main line, the annotated output of job `0aaa055f…` and replay `9dbf6846…`, the endpoint and `build_sha`, the 010 text, and the watcher row.
  - ARCHITECTURE: the reconciliation with `terraform state list` (37 managed resources and 15 data sources) and the worker/finalize sequence diagram.
  - COST and DECISIONS: the watcher on in the deployed stack, and 010.
  - DEMO_SCRIPT: segment 1 timings and segment 5.
- **Checked:**
  - Every figure quoted from WS0's rehearsal log.
  - Both watcher executions, with a read-only `ListExecutions` (SUCCEEDED, 10.2 s each).
  - The resource count (52 entries = 37 managed + 15 data).
  - The worker diagram's conditions, against `qmc_core.ddb`.
  - All five Mermaid diagrams render.
- **Mistakes made and fixed:**
  - The first draft of the worker diagram said `mark_done` was conditional on the status; it is conditional only on the job existing. Fixed before the push.
  - A `;` in a Mermaid message broke the parse. Fixed before the push.
- **Human checked or changed:** James chose the saved email screenshot for segment 5. *Pending: the video link.*
