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
