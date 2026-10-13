#!/usr/bin/env python3
"""
Create the GitLab plan for the Quantum-Seeded Monte Carlo build: labels, milestones (one per gate)
and one issue per ticket in scripts/gitlab_issues.csv. Standard library only.

Usage:
  export GITLAB_TOKEN=glpat-...            # a personal access token with the `api` scope
  export GITLAB_URL=https://labs.gauntletai.com                       # default
  export GITLAB_PROJECT=jamesmcgirr/quantum-seeded-monte-carlo         # default

  python3 scripts/create_gitlab_issues.py --offline              # validate the CSV, no network
  python3 scripts/create_gitlab_issues.py                        # dry run: show what would be created
  python3 scripts/create_gitlab_issues.py --apply --limit 1      # create labels, milestones, one issue; check it
  python3 scripts/create_gitlab_issues.py --apply                # everything else (idempotent)
  python3 scripts/create_gitlab_issues.py --apply --link-deps    # add blocked-by links (needs the issues to exist)

Idempotent: labels, milestones and issues whose name/title already exists are skipped.
Blocked-by links use GitLab's `is_blocked_by` link type and fall back to `relates_to`
on editions where blocking links are not available. Dependencies are also written into each
issue body, so nothing is lost either way.
"""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "gitlab_issues.csv")

GITLAB_URL = os.environ.get("GITLAB_URL", "https://labs.gauntletai.com").rstrip("/")
PROJECT = os.environ.get("GITLAB_PROJECT", "jamesmcgirr/quantum-seeded-monte-carlo")
TOKEN = os.environ.get("GITLAB_TOKEN", "")
PREFIX = "QMC-"

# Gates in US Central time (see docs/HANDOFF.md section 2). GitLab milestones carry dates only,
# and a milestone's due date must not be before its start date.
MILESTONES = {
    "M0": ("M0 - G0 Contract frozen", "2026-10-12", "2026-10-13",
           "Gate G0, Tue 2026-10-13 12:00 PM Central. Day 0 prerequisites done; qmc_core implemented and "
           "tested (coverage >= 90%); skeleton, Makefile, CI green; NumPy and SIMD pins recorded; tag g0-contract-frozen."),
    "M1": ("M1 - G1 Deployed end to end", "2026-10-13", "2026-10-13",
           "Gate G1, Tue 2026-10-13 6:00 PM Central. make apply from empty; make e2e: 1M-path job COMPLETE "
           "within 3 SE of Black-Scholes, then a bit-identical replay; correctness and golden tests green."),
    "M2": ("M2 - G2 Hardened", "2026-10-13", "2026-10-14",
           "Gate G2, Wed 2026-10-14 12:00 PM Central. Every edge case demonstrated on the deployed stack; "
           "PARTIAL + DLQ alarm email; Budget and alarms live; apply/plan/destroy/apply with no drift or orphans."),
    "M3": ("M3 - G3 Proven", "2026-10-14", "2026-10-14",
           "Gate G3, Wed 2026-10-14 4:00 PM Central. Benchmarks, replay x20 bit-identical, coverage >= 80%, "
           "Checkov green with justified suppressions, TEST_RESULTS.md; stretch merged or cut."),
    "M4": ("M4 - G4 Finish line", "2026-10-14", "2026-10-14",
           "Gate G4, Wed 2026-10-14 9:00 PM Central (buffer to 11:59 PM; due Wednesday). README quick-start, "
           "diagram, cost note, AI_USAGE, video <= 5 min, fresh-clone rehearsal, stack deployed, GitHub mirror current."),
}

LABELS = {
    "ws0": "#2c3e50", "ws1": "#1f78b4", "ws2": "#33a02c", "ws3": "#e31a1c", "ws4": "#ff7f00", "ws5": "#6a3d9a",
    "early": "#c0392b", "final": "#f39c12", "stretch": "#95a5a6",
    "never-cut": "#000000",
    "infra": "#7f8c8d", "core": "#8e44ad", "api": "#2980b9", "worker": "#27ae60",
    "verify": "#e67e22", "docs": "#16a085",
}
OWNERS = {"ws0", "ws1", "ws2", "ws3", "ws4", "ws5"}
GATES = {"early", "final", "stretch"}
AREAS = {"infra", "core", "api", "worker", "verify", "docs"}
WS_NAMES = {
    "ws0": "WS0 Foundation and contract", "ws1": "WS1 Infrastructure", "ws2": "WS2 API",
    "ws3": "WS3 Worker", "ws4": "WS4 Verification", "ws5": "WS5 Docs and demo",
}


def load_rows():
    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (int(r["priority"]), r["id"]))
    return rows


def deps_of(row):
    return [d.strip() for d in row["blocked_by"].split(",") if d.strip()]


def validate(rows):
    problems = []
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate ticket id")
    known = set(ids)
    for r in rows:
        if not r["id"].startswith(PREFIX):
            problems.append(f'{r["id"]}: id must start with {PREFIX}')
        if r["owner"] not in OWNERS:
            problems.append(f'{r["id"]}: unknown owner {r["owner"]}')
        if r["gate"] not in GATES:
            problems.append(f'{r["id"]}: unknown gate {r["gate"]}')
        if r["area"] not in AREAS:
            problems.append(f'{r["id"]}: unknown area {r["area"]}')
        if r["milestone"] not in MILESTONES:
            problems.append(f'{r["id"]}: unknown milestone {r["milestone"]}')
        for d in deps_of(r):
            if d not in known:
                problems.append(f'{r["id"]}: blocked by unknown ticket {d}')
            if d == r["id"]:
                problems.append(f'{r["id"]}: blocks itself')
    graph = {r["id"]: deps_of(r) for r in rows}
    state = {}

    def visit(n, stack):
        if state.get(n) == 1:
            problems.append("dependency cycle: " + " -> ".join(stack + [n]))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in graph.get(n, []):
            visit(d, stack + [n])
        state[n] = 2

    for n in graph:
        visit(n, [])
    return problems


class GitLab:
    def __init__(self, url, project, token):
        self.base = f"{url}/api/v4/projects/{urllib.parse.quote(project, safe='')}"
        self.token = token

    def _req(self, method, path, data=None, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers={
            "PRIVATE-TOKEN": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw.decode(errors="replace")
            return e.code, payload, e.headers

    def list_all(self, path, params=None):
        out, page = [], 1
        while True:
            p = dict(params or {}, per_page=100, page=page)
            status, data, headers = self._req("GET", path, params=p)
            if status != 200:
                raise SystemExit(f"GET {path} failed: {status} {data}")
            out.extend(data)
            nxt = headers.get("X-Next-Page")
            if not nxt:
                return out
            page = int(nxt)

    def post(self, path, data):
        return self._req("POST", path, data=data)


def issue_title(r):
    return f'[{r["id"]}] {r["title"]}'


def ticket_of(title):
    return title[1:title.index("]")] if title.startswith("[" + PREFIX) else None


def issue_body(r):
    deps = deps_of(r)
    blocked = f'\n\n**Blocked by:** {", ".join(deps)}' if deps else ""
    never = "  ·  **Never cut**" if r.get("never_cut", "").strip().lower() == "yes" else ""
    return (f'{r["description"]}{blocked}\n\n---\n'
            f'**Workstream:** {WS_NAMES[r["owner"]]}  ·  **Gate:** {r["gate"]}  ·  '
            f'**Priority:** {r["priority"]}{never}  ·  **Ticket:** {r["id"]}\n\n'
            f'Spec: `docs/HANDOFF.md` · Prompt: `docs/prompts/{r["owner"].upper()}-*.md`')


def labels_for(r):
    labels = [r["owner"], r["gate"], r["area"]]
    if r.get("never_cut", "").strip().lower() == "yes":
        labels.append("never-cut")
    return labels


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to GitLab (default is a dry run)")
    ap.add_argument("--limit", type=int, default=None, help="create at most N issues")
    ap.add_argument("--link-deps", action="store_true", help="add blocked-by links between existing issues")
    ap.add_argument("--offline", action="store_true", help="validate the CSV only")
    args = ap.parse_args()

    rows = load_rows()
    problems = validate(rows)
    if problems:
        print("CSV problems:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print(f"CSV ok: {len(rows)} tickets, {len(MILESTONES)} milestones, {len(LABELS)} labels")
    if args.offline:
        return
    if not TOKEN:
        sys.exit("Set GITLAB_TOKEN (a personal access token with the api scope).")

    gl = GitLab(GITLAB_URL, PROJECT, TOKEN)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] {GITLAB_URL} · {PROJECT}")

    existing_labels = {l["name"] for l in gl.list_all("/labels")}
    for name, color in LABELS.items():
        if name not in existing_labels:
            print(f"  label + {name}")
            if args.apply:
                s, d, _ = gl.post("/labels", {"name": name, "color": color})
                if s not in (200, 201):
                    sys.exit(f"label {name} failed: {s} {d}")

    ms_by_title = {m["title"]: m for m in gl.list_all("/milestones", {"state": "all"})}
    ms_id = {}
    for key, (title, start, due, desc) in MILESTONES.items():
        if title in ms_by_title:
            ms_id[key] = ms_by_title[title]["id"]
            continue
        print(f"  milestone + {title} (due {due})")
        if args.apply:
            s, d, _ = gl.post("/milestones", {"title": title, "description": desc,
                                              "start_date": start, "due_date": due})
            if s not in (200, 201):
                sys.exit(f"milestone {title} failed: {s} {d}")
            ms_id[key] = d["id"]

    issues = {i["title"]: i for i in gl.list_all("/issues", {"state": "all", "scope": "all"})}
    made = 0
    for r in rows:
        title = issue_title(r)
        if title in issues:
            continue
        if args.limit is not None and made >= args.limit:
            break
        print(f"  issue + {title}")
        made += 1
        if args.apply:
            payload = {"title": title, "description": issue_body(r), "labels": ",".join(labels_for(r))}
            if r["milestone"] in ms_id:
                payload["milestone_id"] = ms_id[r["milestone"]]
            s, d, _ = gl.post("/issues", payload)
            if s not in (200, 201):
                sys.exit(f"issue {title} failed: {s} {d}")
            issues[title] = d
    print(f"  {made} issue(s) {'created' if args.apply else 'would be created'}")

    # QMC id -> GitLab issue number (iids follow creation order, not QMC numbers)
    mapping = sorted((ticket_of(t), i["iid"], i.get("web_url", "")) for t, i in issues.items() if ticket_of(t))
    if mapping:
        with open(os.path.join(HERE, "issue_map.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ticket", "issue", "url"])
            w.writerows(mapping)
        print("  QMC -> issue map written to scripts/issue_map.csv")

    if args.link_deps:
        by_ticket = {ticket_of(t): i for t, i in issues.items() if ticket_of(t)}
        link_type = "is_blocked_by"
        for r in rows:
            src = by_ticket.get(r["id"])
            if not src:
                continue
            existing = gl._req("GET", f'/issues/{src["iid"]}/links')[1] or []
            linked = {x.get("iid") for x in existing if isinstance(x, dict)}
            for dep in deps_of(r):
                tgt = by_ticket.get(dep)
                if not tgt or tgt["iid"] in linked:
                    continue
                print(f'  link {r["id"]} {link_type} {dep}')
                if not args.apply:
                    continue
                s, d, _ = gl.post(f'/issues/{src["iid"]}/links', {
                    "target_project_id": src["project_id"], "target_issue_iid": tgt["iid"], "link_type": link_type})
                if s in (400, 403) and link_type != "relates_to":
                    print("  blocking links unavailable on this edition; using relates_to")
                    link_type = "relates_to"
                    s, d, _ = gl.post(f'/issues/{src["iid"]}/links', {
                        "target_project_id": src["project_id"], "target_issue_iid": tgt["iid"],
                        "link_type": link_type})
                if s not in (200, 201, 409):
                    print(f"    link failed: {s} {d}")


if __name__ == "__main__":
    main()
