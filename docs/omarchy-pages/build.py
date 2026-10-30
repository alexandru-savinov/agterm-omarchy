#!/usr/bin/env python3
"""Build a static coordination site from a public GitHub issue snapshot."""
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shutil

REPOSITORY = "alexandru-savinov/agterm-omarchy"
BASE = "https://github.com/" + REPOSITORY
PHASES = {"working": "Work reported", "assigned": "Assigned · acknowledgment pending",
          "awaiting-review": "Awaiting review / decision", "completed": "Completed report",
          "coordination": "Coordination reported", "historical": "Historical / superseded scope"}
STREAMS = {"integration": "Integration", "implementation": "Implementation",
           "validation": "Validation", "review": "Review"}


def roster_snapshot(data):
    """Validate a reviewed snapshot; never interpret comments or remote HTML."""
    def timestamp(value):
        if not isinstance(value, str):
            raise ValueError("roster timestamps must be UTC strings")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("roster timestamps must include UTC")
        return parsed
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        raise ValueError("roster must contain an agents array")
    observed = timestamp(data.get("observed_at"))
    agents, identities = [], set()
    for entry in data["agents"]:
        if not isinstance(entry, dict):
            raise ValueError("roster entry must be an object")
        name, account = entry.get("name"), entry.get("account")
        if not all(isinstance(value, str) and re.fullmatch(r"[a-z0-9_-]+", value)
                   for value in (name, account)) or (account, name) in identities:
            raise ValueError("invalid or duplicate roster identity")
        identities.add((account, name))
        number, kind = entry.get("task"), entry.get("task_kind")
        source = entry.get("source")
        if type(number) is not int or number < 1 or kind not in ("issues", "pull"):
            raise ValueError("invalid roster task")
        if not isinstance(source, str) or not re.fullmatch(
                r"/(?:issues|pull)/[1-9][0-9]*(?:#(?:issuecomment|pullrequestreview)-[1-9][0-9]*)?", source):
            raise ValueError("roster source must be a repository issue or PR reference")
        if entry.get("phase") not in PHASES or entry.get("reported_state") not in (
                None, "working", "blocked", "ready-for-review", "complete"):
            raise ValueError("invalid roster phase or reported state")
        if not isinstance(entry.get("work"), str) or not entry["work"].strip():
            raise ValueError("roster work summary is required")
        if entry.get("workstream") not in STREAMS or not isinstance(entry.get("role"), str) or not entry["role"].strip():
            raise ValueError("roster role and workstream are required")
        if timestamp(entry.get("reported_at")) > observed:
            raise ValueError("roster source cannot postdate its snapshot")
        if entry["phase"] == "assigned" and entry["reported_state"] is not None:
            raise ValueError("unacknowledged assignment cannot claim a reported state")
        agents.append({**entry, "source_url": BASE + source, "task_url": f"{BASE}/{kind}/{number}"})
    return {"observed_at": data["observed_at"], "agents": agents}


def tasks_from_pages(pages, limit):
    if not isinstance(pages, list):
        raise ValueError("issue snapshot must be an array")
    issues = [issue for page in pages for issue in page] if pages and isinstance(pages[0], list) else pages
    tasks = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = issue.get("number")
        if type(number) is not int or number < 1:
            continue
        state = issue.get("state")
        if state not in ("open", "closed"):
            continue
        tasks.append({"number": number, "title": str(issue.get("title", "Untitled task")), "state": state})
    return sorted(tasks, key=lambda task: (task["state"] != "open", task["number"]))[:limit]


QUEUE_STATE_PHASE = {"working": "working", "blocked": "awaiting-review",
                     "ready-for-review": "awaiting-review", "complete": "completed"}
QUEUE_MAX_AGE_SECONDS = 2 * 3600


def hours(seconds):
    whole, rest = divmod(int(seconds), 3600)
    return f"{whole}h{rest // 60:02d}m"


def queue_overlay(roster, queue, now):
    """Overlay the automatic queue snapshot on the reviewed roster.

    The roster keeps its reviewed prose (role, workstream, work summary). The queue
    supplies the newest self-reported task, state, age and stale flag per account/name.
    Returns (agents, queue-only agents, freshness note or None)."""
    latest, stale = {}, {}
    for claim in queue.get("claims", []):
        record = claim.get("latest") or {}
        url = record.get("url") or ""
        if not url.startswith(BASE + "/issues/") or record.get("created_at") is None:
            continue  # only this repository's issue comments, never remote links
        key = (str(claim.get("account", "")).lower(), str(claim.get("name", "")))
        entry = {"task": claim.get("task"), "state": record.get("state"), "url": url,
                 "created_at": record["created_at"], "age_seconds": record.get("age_seconds"),
                 "stale": bool(record.get("stale")),
                 # Same ordering as scripts/github/queue.py observation_order: (updated_at, url).
                 "order": (record.get("updated_at") or record["created_at"], url)}
        if entry["stale"]:
            stale.setdefault(key, []).append(entry)
        if key not in latest or entry["order"] > latest[key]["order"]:
            latest[key] = entry
    # A stale claim on another task must stay visible even when a newer claim is shown.
    for key, entry in latest.items():
        entry["other_stale"] = sorted((item for item in stale.get(key, []) if item is not entry),
                                      key=lambda item: item["task"])
    agents, seen = [], set()
    for agent in roster["agents"]:
        key = (agent["account"].lower(), agent["name"])
        seen.add(key)
        agents.append({**agent, "queue": latest.get(key)})
    extras = [{"account": account, "name": name, **entry}
              for (account, name), entry in sorted(latest.items()) if (account, name) not in seen]
    observed = datetime.fromisoformat(queue["observed_at"].replace("Z", "+00:00"))
    age = (now - observed).total_seconds()
    note = (f"Queue snapshot is {hours(age)} old; claim ages may be behind."
            if age > QUEUE_MAX_AGE_SECONDS else None)
    return agents, extras, note


def queue_line(entry):
    if entry is None:
        return '<p class="queue muted">No check-in for this name in the queue snapshot.</p>'
    age = "" if entry["age_seconds"] is None else f' · {hours(entry["age_seconds"])} since check-in'
    stale = ' · <strong class="stale">stale: no check-in for over 4h</strong>' if entry["stale"] else ""
    others = "".join(
        f' <a href="{escape(item["url"], quote=True)}">#{int(item["task"])}</a> ({hours(item["age_seconds"] or 0)})'
        for item in entry.get("other_stale", []))
    others = f'<br><strong class="stale">Also stale:</strong>{others}' if others else ""
    return (f'<p class="queue">Queue: <a href="{escape(entry["url"], quote=True)}">#{int(entry["task"])}</a>'
            f' · {escape(entry["state"] or "unknown")}{age}{stale}{others}</p>')


def team_page(roster, rows, commit):
    phases = "".join(f'<option value="{key}">{label}</option>' for key, label in PHASES.items())
    streams = "".join(f'<option value="{key}">{label}</option>' for key, label in STREAMS.items())
    count = len(roster["agents"])
    completed = sum(agent["phase"] == "completed" for agent in roster["agents"])
    reviewing = sum(agent["phase"] == "awaiting-review" for agent in roster["agents"])
    observed = escape(roster["observed_at"], quote=True)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="description" content="Who is doing what on agterm for Omarchy: a source-linked snapshot of responsibilities, reviews and the release gate.">
<title>Team room · agterm for Omarchy</title><link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="team.css"><script src="team.js" defer></script></head>
<body><a class="skip" href="#main">Skip to team room</a>
<header><a class="wordmark" href="./">agterm<span>/omarchy</span></a><nav aria-label="Primary"><a href="./">Project hub</a><a href="#roster">The roster</a><a href="{BASE}/issues/1">GitHub hub ↗</a></nav></header>
<main id="main"><section class="hero" aria-labelledby="title"><div class="hero-copy"><p class="eyebrow">Team room / 01</p>
<h1 id="title">Different agents.<br><span>Shared direction.</span></h1><p class="lede">Who is building, checking, reviewing and connecting the pieces of agterm for Omarchy.</p>
<p class="snapshot"><span class="snapshot-mark" aria-hidden="true">▣</span> Evidence snapshot <time datetime="{observed}">{observed}</time></p></div>
<aside class="terminal" aria-label="Snapshot overview"><div class="terminal-bar"><span aria-hidden="true">● ● ●</span> team / status</div><div class="terminal-body"><p class="command">snapshot / reported ownership</p><dl class="numbers"><div><dt>named contributors</dt><dd>{count:02}</dd></div><div><dt>review / decision handoffs</dt><dd>{reviewing:02}</dd></div><div><dt>completed reports</dt><dd>{completed:02}</dd></div></dl><p class="terminal-note">A record of reports.<br>No online-status guessing.</p></div></aside></section>
<section class="pipeline-section" aria-labelledby="pipeline-title"><div class="section-top"><p class="eyebrow">How the work moves</p><h2 id="pipeline-title">Every handoff leaves a trace.</h2></div>
<ol class="pipeline"><li><span class="step">01 / implement</span><strong>Make it portable</strong><p>CPU baseline, queue behavior, and this team page.</p></li><li><span class="step">02 / validate</span><strong>Test the actual bytes</strong><p>Hosted builds, native Wayland, and macOS core checks.</p></li><li><span class="step">03 / review</span><strong>Challenge the claims</strong><p>Read-only audits and source-linked findings.</p></li><li><span class="step">04 / publish</span><strong>Root owns the gate</strong><p>Review, merge, deployment and release decisions.</p></li></ol></section>
<section class="gate" aria-labelledby="gate-title"><div><p class="eyebrow">Release gate / blocked</p><h2 id="gate-title">Builds pass.<br>Live needs fresh acceptance.</h2><p>Bundled zmx in the hosted ARM64 payload hits SIGILL on Omarchy. CPU portability fix <code>5142c7b8</code> is pushed; its <a href="{BASE}/actions/runs/41030003211">trusted hosted matrix is underway</a>. Fresh fixed-payload acceptance remains required; a stable release is not claimed.</p><a href="{BASE}/issues/52#issuecomment-6512330102">Follow the zmx work on #52 →</a><h3 class="graphics-heading">New / unresolved graphics report</h3><p>A user reports an OpenGL launch failure. <a href="{BASE}/issues/56">#56</a> tracks read-only diagnosis; the exact latest error and machine are not yet matched. Prior UTM acceptance used process-local software rendering and does not establish default-launch graphics compatibility. This report is separate from confirmed zmx SIGILL.</p></div>
<div class="evidence-stack"><article><span class="proof historical">Diagnostic / modified copy</span><h3>9 checks after zmx substitution</h3><p>Root reports nine Live checks and an independent cleanup audit after replacing only zmx. This isolates the component; it does not accept the original archive.</p><a href="{BASE}/issues/53#issuecomment-6518901862">Read the diagnostic handoff ↗</a></article><article><span class="proof good">Passed / hosted</span><h3>ARM64 + x86_64 matrix</h3><p>Exact source <code>fa64d44f</code>. Hosted CI is a build/test result, not native acceptance.</p><a href="{BASE}/actions/runs/40957588704">Open the final-source run ↗</a></article><article><span class="proof good">Passed / ordinary native</span><h3>7 Wayland checks</h3><p>The unchanged hosted ARM64 archive passed ordinary mode with bash. Live remains blocked.</p><a href="{BASE}/issues/52#issuecomment-6512184075">Read the operator report ↗</a></article><article><span class="proof historical">Historical / different payload</span><h3>9 native Live checks</h3><p>Native-built <code>5e37cb75</code>, using a zsh fixture and software rendering. It does not clear the hosted payload’s gate.</p><a href="{BASE}/tree/main/docs/evidence/omarchy-arm64-2026-10-29">Inspect the retained evidence ↗</a></article></div></section>
<section id="roster" aria-labelledby="roster-title"><div class="section-top"><p class="eyebrow">People-shaped responsibilities / agent-sized work</p><h2 id="roster-title">Who is doing what</h2><p class="roster-intro">Roles describe scope. Markers describe the last report. Source discussions decide ownership.</p></div>
<div class="filters" hidden><label>Find a name, role or task<input id="search" type="search" placeholder="Try portability, review, root…"></label><label>Reported phase<select id="phase"><option value="">All phases</option>{phases}</select></label><label>Workstream<select id="stream"><option value="">All workstreams</option>{streams}</select></label><button id="reset" type="button">Reset</button></div>
<p id="result-count" class="result-count" role="status" aria-live="polite">{count} contributors in this snapshot</p><p id="no-results" hidden>No matching reports. Reset the filters to see the full roster.</p>
<ul class="roster">{rows}</ul></section>
<section class="join-panel"><div><p class="eyebrow">There is room for another perspective</p><h2>Pick a task.<br>Bring back evidence.</h2></div><div><p>Read the latest thread, name your scope, and coordinate before editing. Review work can be valuable without claiming source ownership.</p><a class="button" href="{BASE}/issues/1">Join through the GitHub hub ↗</a> <a href="./#join">Copy the check-in format</a></div></section>
<aside class="snapshot-notes" aria-label="How to read this snapshot"><strong>Read the timestamp, not an implied heartbeat.</strong><p>Reported state is not proof of present liveness. All listed names use the shared <code>alexandru-savinov</code> GitHub account; they are not separate GitHub identities or independent approvals. An open issue can contain completed historical work. Assignment, acknowledgment and review are distinct. This curated snapshot changes through review, not live polling; follow the linked source before acting. Native x86_64 Omarchy runtime acceptance remains unverified.</p></aside>
</main><footer><a class="wordmark" href="./">agterm<span>/omarchy</span></a><p>Source <a href="{BASE}/commit/{commit}">{commit[:8]}</a> · <a href="status.json">Machine-readable snapshot</a> · <a href="{BASE}/issues/53">Page task #53</a></p></footer></body></html>'''


def write_site(destination, pages, commit, limit=25, roster=None, queue=None):
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise ValueError("commit must be a hexadecimal Git commit ID")
    if limit not in (10, 25, 50):
        raise ValueError("task limit must be 10, 25, or 50")
    tasks = tasks_from_pages(pages, limit)
    roster = roster_snapshot(roster if roster is not None else
                             json.loads(Path(__file__).with_name("roster.json").read_text()))
    generated_at = datetime.now(timezone.utc)
    queue_note, extras = None, []
    if queue is not None:
        agents, extras, queue_note = queue_overlay(roster, queue, generated_at)
        roster = {**roster, "agents": agents}
    roster_rows = "\n".join(
        f'<li class="roster-card" data-phase="{agent["phase"]}" data-stream="{agent["workstream"]}">'
        f'<p class="stream">{STREAMS[agent["workstream"]]} / {escape(agent["role"])}</p>'
        f'<div class="roster-heading"><h3>{escape(agent["name"])}</h3>'
        f'<span class="phase">{PHASES[agent["phase"]]}</span></div>'
        f'<p><a href="{agent["task_url"]}">{"PR" if agent["task_kind"] == "pull" else "Task"} '
        f'#{agent["task"]}</a> · Last reported state: '
        f'<strong>{escape(agent["reported_state"] or "not reported")}</strong></p>'
        + (queue_line(agent["queue"]) if queue is not None else "")
        + f'<p>{escape(agent["work"])}</p><p class="muted">'
        f'<a href="{agent["source_url"]}">Source report / assignment</a> · '
        f'<time datetime="{escape(agent["reported_at"], quote=True)}">{escape(agent["reported_at"])}</time>'
        f'<br>GitHub account: {escape(agent["account"])}</p></li>'
        for agent in roster["agents"])
    roster_rows += "".join(
        f'\n<li class="roster-card queue-only" data-phase="{QUEUE_STATE_PHASE.get(entry["state"], "coordination")}" data-stream="">'
        f'<p class="stream">Queue only / not in the reviewed roster</p>'
        f'<div class="roster-heading"><h3>{escape(entry["name"])}</h3></div>{queue_line(entry)}'
        f'<p class="muted">GitHub account: {escape(entry["account"])}</p></li>'
        for entry in extras)
    if queue_note:
        roster_rows = f'<li class="queue-note">{escape(queue_note)}</li>\n' + roster_rows
    generated = generated_at.isoformat(timespec="seconds")
    rows = "\n".join(
        f'<li><a href="{BASE}/issues/{task["number"]}"><span class="number">#{task["number"]}</span>'
        f'<span>{escape(task["title"])}</span></a><span class="state {task["state"]}">{task["state"]}</span></li>'
        for task in tasks
    ) or '<li>No tasks were included in this snapshot. Open the live issue queue below.</li>'
    content = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="description" content="Development hub, agent coordination, and readiness evidence for the agterm Omarchy port.">
<title>agterm for Omarchy · Development hub</title><link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="style.css"></head>
<body><a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="./">agterm <span>/ Omarchy</span></a><nav aria-label="Primary"><a href="team.html">Team room</a><a href="#join">Join</a><a href="#tasks">Tasks</a><a href="{BASE}">Repository ↗</a></nav></header>
<main id="main"><section class="hero"><p class="eyebrow">A Linux terminal port, built in public</p>
<h1>One place to work.<br>One record of progress.</h1>
<p class="lead">Bringing agterm’s workspace and session model to Omarchy, with native ARM64 and x86_64 builds and real Hyprland testing.</p>
<div class="actions"><a class="button" href="{BASE}/issues/1">Open the orchestration hub</a><a href="{BASE}/pull/6">Review the port →</a></div></section>
<section class="readiness" aria-labelledby="readiness-title"><div><p class="eyebrow">Readiness</p><h2 id="readiness-title">Work in progress</h2></div><div><p>This is a development port, not a verified stable release. The hosted ARM64 and x86_64 matrix passed at <code>fa64d44f</code>. That exact hosted ARM64 archive passed seven ordinary native Wayland checks; its Live path remains blocked by bundled zmx SIGILL (illegal instruction), tracked in <a href="{BASE}/issues/52">#52</a>. A copied payload with only zmx replaced passed nine checks and an independent cleanup audit; fresh fixed-payload acceptance is still required. The older native-built installation passed nine Wayland/Live checks at <code>5e37cb75</code>; it is a different payload and does not clear this blocker.</p><p>The UTM tests use process-local software rendering; Live tests use a zsh login-shell fixture. A separate <a href="{BASE}/issues/56">OpenGL launch report</a> remains unresolved; software-rendering tests do not establish default-launch graphics compatibility. CPU fix <code>5142c7b8</code> is in fresh hosted CI. Native x86_64 Omarchy runtime acceptance remains unverified. The shared Linux baseline is <strong>agterm v0.35.1</strong>; this documentation deployment is not another runtime test.</p><a href="{BASE}/actions/runs/40957588704">Final-source hosted matrix</a> · <a href="{BASE}/issues/52#issuecomment-6512184075">Seven-check operator report</a> · <a href="{BASE}/tree/main/docs/evidence/omarchy-arm64-2026-10-29">Older native evidence</a> · <a href="{BASE}/issues/17">Track the prerelease</a></div></section>
<section id="team" aria-labelledby="team-title"><p class="eyebrow">Inside the team room</p><h2 id="team-title">Who is doing what</h2><p>{len(roster['agents'])} named contributors. Implementation, validation, review and integration, with responsibilities and source-linked handoffs.</p><p class="muted">Evidence snapshot: {escape(roster['observed_at'])}. Reported work is not proof of present liveness.</p><a class="button" href="team.html">Explore the team room →</a></section>
<section id="join" class="join"><div><p class="eyebrow">Contribute as a person or an agent</p><h2>Claim a task.<br>Leave evidence.</h2><p>GitHub issues are the task queue and communication record. Start in the hub, identify your execution environment, then claim an issue and the files you own before editing.</p></div><div><pre><code>Agent: &lt;name&gt;
Task: #&lt;number&gt;
Owned paths: &lt;paths&gt;
State: working
Connected: &lt;environment / runner&gt;
Evidence: &lt;test or artifact&gt;
Next: &lt;concrete action&gt;</code></pre><p>Post progress, blockers, test commands, and handoffs on the task issue. Link pull requests and artifacts. A shared GitHub login is not an independent reviewer. Keep credentials and private terminal content out of public evidence.</p><a href="{BASE}/issues/1">Post your check-in →</a></div></section>
<section id="tasks"><div class="section-heading"><div><p class="eyebrow">Work queue snapshot</p><h2>Follow the tasks</h2></div><a href="{BASE}/issues">Live issue queue ↗</a></div><ul class="tasks">{rows}</ul><p class="muted">Snapshot generated {escape(generated)}. Issue status can change after this page is built; the linked issue is authoritative.</p></section>
<section class="evidence" aria-labelledby="evidence-title"><p class="eyebrow">Proof, not promises</p><h2 id="evidence-title">Inspect the evidence</h2><div class="cards"><a href="{BASE}/actions/workflows/ci.yml"><h3>Builds and checks ↗</h3><p>Hosted matrix results, logs, and downloadable build artifacts. Check the commit and architecture.</p></a><a href="{BASE}/tree/main/docs/evidence/omarchy-arm64-2026-10-29"><h3>Native desktop ↗</h3><p>Omarchy VM installation, Wayland rendering, PTY input, and overlay acceptance.</p></a><a href="{BASE}/issues/4"><h3>GitHub feature inventory ↗</h3><p>Which capabilities have evidence, which are only enabled, and which remain unavailable.</p></a></div></section>
</main><footer><p>Built from <a href="{BASE}/commit/{commit}">{commit[:8]}</a> · <a href="status.json">Machine-readable snapshot</a></p><p>Based on <a href="https://github.com/melonamin/agterm-linux">melonamin/agterm-linux</a> and <a href="https://github.com/umputun/agterm">upstream agterm</a>. This site documents the Omarchy port.</p></footer></body></html>
'''
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.html").write_text(content)
    (destination / "team.html").write_text(team_page(roster, roster_rows, commit))
    shutil.copyfile(Path(__file__).with_name("style.css"), destination / "style.css")
    for asset in ("team.css", "team.js", "favicon.svg"):
        shutil.copyfile(Path(__file__).with_name(asset), destination / asset)
    (destination / ".nojekyll").touch()
    (destination / "status.json").write_text(json.dumps({
        "repository": REPOSITORY, "readiness": "work-in-progress", "upstream_baseline": "v0.35.1",
        "commit": commit, "generated_at": generated, "tasks": tasks, "roster": roster,
        "queue": None if queue is None else {"observed_at": queue["observed_at"], "note": queue_note,
                                            "queue_only": extras},
    }, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--limit", type=int, choices=(10, 25, 50), default=25)
    parser.add_argument("--queue", type=Path, help="queue.json from scripts/github/queue.py (optional)")
    args = parser.parse_args()
    queue = json.loads(args.queue.read_text()) if args.queue and args.queue.exists() else None
    write_site(args.output, json.loads(args.issues.read_text()), args.commit, args.limit, queue=queue)


if __name__ == "__main__":
    main()
