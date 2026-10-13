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
  - *Not yet:* someone signed in has not confirmed the Mermaid diagrams render in GitLab's viewer.
