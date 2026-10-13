#!/usr/bin/env bash
# One-time setup after reviewing the planning docs. Run from the main checkout on your Mac:
#
#   ./scripts/bootstrap.sh            # dry run: prints every step, changes nothing
#   ./scripts/bootstrap.sh --apply    # commit, push, branches, worktrees, GitLab plan
#
# Steps (each is skipped if already done):
#   1. commit the planning docs on main and push
#   2. create and push one branch per workstream (ws0-foundation ... ws5-docs)
#   3. add a worktree per workstream next to this checkout (../quantum-seeded-monte-carlo-wsN)
#   4. create GitLab labels, milestones and issues, then link dependencies
#      (needs GITLAB_TOKEN; skipped with a note if it is not set)
#   5. create the shared Terraform state folder ($HOME/.qmc/state)
set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

ROOT="$(git rev-parse --show-toplevel)"
NAME="$(basename "$ROOT")"
PARENT="$(dirname "$ROOT")"
cd "$ROOT"

run() {
  echo "+ $*"
  if [[ $APPLY -eq 1 ]]; then "$@"; fi
}

BRANCHES=(ws0-foundation ws1-infra ws2-api ws3-worker ws4-verify ws5-docs)

echo "== 0. validate the ticket CSV"
python3 scripts/create_gitlab_issues.py --offline

echo "== 1. commit planning docs on main"
current="$(git branch --show-current)"
if [[ "$current" != "main" ]]; then
  echo "  on $current, not main: skipping the commit (re-run from main if the planning docs are not committed yet)"
else
  if [[ -n "$(git status --porcelain)" ]]; then
    run git add -A
    run git commit -m "WS0: research primer, PRD, handoff, roadmap, workstream prompts, GitLab plan" \
      -m "Planning output from the research and decision session of 2026-10-12." \
      -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  else
    echo "  nothing to commit"
  fi
  run git push origin main
fi

echo "== 2. workstream branches"
for b in "${BRANCHES[@]}"; do
  if git show-ref --verify --quiet "refs/heads/$b"; then
    echo "  $b exists"
  else
    run git branch "$b" main
  fi
  run git push -u origin "$b"
done

echo "== 3. worktrees"
for i in 1 2 3 4 5; do
  b="${BRANCHES[$i]}"
  path="$PARENT/$NAME-ws$i"
  if [[ -d "$path" ]]; then
    echo "  $path exists"
  else
    run git worktree add "$path" "$b"
  fi
done
echo "  WS0 works in this checkout: git switch ws0-foundation"

echo "== 4. GitLab labels, milestones, issues"
if [[ -z "${GITLAB_TOKEN:-}" ]]; then
  echo "  GITLAB_TOKEN not set; run later:"
  echo "    GITLAB_TOKEN=glpat-... python3 scripts/create_gitlab_issues.py --apply"
  echo "    GITLAB_TOKEN=glpat-... python3 scripts/create_gitlab_issues.py --apply --link-deps"
else
  if [[ $APPLY -eq 1 ]]; then
    python3 scripts/create_gitlab_issues.py --apply
    python3 scripts/create_gitlab_issues.py --apply --link-deps
    if [[ "$(git branch --show-current)" == "main" && -n "$(git status --porcelain scripts/issue_map.csv)" ]]; then
      git add scripts/issue_map.csv
      git commit -m "WS0: GitLab issue map" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
      git push origin main
    fi
  else
    python3 scripts/create_gitlab_issues.py
  fi
fi

echo "== 5. shared Terraform state and settings folder (outside every worktree)"
run mkdir -p "$HOME/.qmc/state"
run chmod 700 "$HOME/.qmc"
if [[ ! -f "$HOME/.qmc/dev.tfvars" ]]; then
  echo "  create $HOME/.qmc/dev.tfvars (chmod 600) with alert_email, create_grader_user = true,"
  echo "  enable_fault_injection = true, and worker_reserved_concurrency = 10 once the quota is >= 1000"
fi

echo
git worktree list
[[ $APPLY -eq 1 ]] || echo "(dry run: re-run with --apply to make these changes)"
