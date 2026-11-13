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
TASK_LIMITS = (10, 25, 50, 100)
DEFAULT_TASK_LIMIT = 100
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
# Same set as scripts/github/queue.py TRUSTED_ASSOCIATIONS: only these claims become cards.
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
QUEUE_UNAVAILABLE = "Queue snapshot unavailable; showing curated phases only."


def hours(seconds):
    whole, rest = divmod(int(seconds), 3600)
    return f"{whole}h{rest // 60:02d}m"


def queue_overlay(roster, queue, now):
    """Overlay the automatic queue snapshot on the reviewed roster.

    The roster keeps its reviewed prose (role, workstream, work summary). The queue
    supplies the newest self-reported task, state, age and stale flag per account/name.
    Only collaborator claims are overlaid; others are counted, never shown as cards.
    Returns (agents, queue-only agents, freshness note or None, unverified claim count)."""
    latest, stale = {}, {}
    unverified = len(queue.get("unverified_claims") or [])
    for claim in queue.get("claims", []):
        record = claim.get("latest") or {}
        url = record.get("url") or ""
        if not url.startswith(BASE + "/issues/") or record.get("created_at") is None:
            continue  # only this repository's issue comments, never remote links
        if record.get("author_association") not in TRUSTED_ASSOCIATIONS:
            unverified += 1  # fail closed: a claim without collaborator standing never becomes a card
            continue
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
    return agents, extras, note, unverified


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


def displayed_phase(agent):
    claim = agent.get("queue")
    return QUEUE_STATE_PHASE.get(claim["state"], "coordination") if claim else agent["phase"]


def current_validation():
    """Keep the current goal visible independently of the bounded task snapshot."""
    return f'''<section class="readiness" id="current-validation" aria-labelledby="current-validation-title">
<div><p class="eyebrow">Current validation / work in progress</p><h2 id="current-validation-title">Current acceptance remains incomplete.</h2><a class="button" href="{BASE}/issues/305">Follow validation goal #305 →</a></div>
<div><p>The <a href="{BASE}/pull/317">current Mac and native evidence</a> keeps each tested subject separate. The historical acceptance below does not certify current <code>main</code>.</p>
<p><strong>Frozen artifact:</strong> <a href="{BASE}/commit/bec375d70025ce3ec0e93fb9a0f3bb3be5ecae9d"><code>bec375d7</code></a> passed ordinary and zsh-fixture Live checks, but its original configuration shutdown failed. Actual bash testing demonstrated the documented non-zsh Live fallback, not bash Live support.</p>
<p><strong>PR #322 hybrid:</strong> successful diagnostic replays use a private frozen-payload copy with one changed library. They do not accept the unchanged frozen artifact or a full-source candidate. <strong>Full-source candidate:</strong> the <a href="{BASE}/pull/322#issuecomment-6808145530">complete source77 native result</a> passed ordinary 7/7, zsh-fixture Live 9/9 and five configuration runs at 4/4 under software rendering. Later integrated-candidate and trusted-artifact acceptance remains incomplete; neither this page build nor a hosted pass supplies native acceptance, and these software-rendered results do not establish hardware rendering.</p>
<p><a href="{BASE}/issues/199">Native lifecycle gate #199</a> · <a href="{BASE}/issues/310">Mac fixture gate #310</a> · <a href="{BASE}/issues/320">Shutdown fix #320</a>. Mac core tests, installed-app comparisons and a source-built Mac app remain separate subjects. <a href="{BASE}/issues/1">Public task claims</a> describe reported work, not proof of running processes; same-account review is not independent approval.</p></div></section>'''


UPSTREAM = "melonamin/agterm-linux"
UPSTREAM_BRANCH = "linux-port"
DRIFT_WORKFLOW = f"{BASE}/actions/workflows/upstream-drift.yml"
DRIFT_MAX_AGE_DAYS = 8  # the drift workflow runs weekly; one missed run makes the snapshot stale


def latest_drift_run(runs):
    """Newest run from `gh run list --json databaseId,createdAt,event,headBranch` that measured main.

    `--branch main` alone also matches a pull request from a fork's branch named main, whose artifact
    the fork controls, so only scheduled and dispatched runs count."""
    trusted = [run for run in runs if isinstance(run, dict) and run.get("headBranch") == "main"
               and run.get("event") in ("schedule", "workflow_dispatch")] if isinstance(runs, list) else []
    return max(trusted, key=lambda run: str(run.get("createdAt")), default=None)


def drift_snapshot(run, drift, now):
    """Validate the latest upstream-drift run and its artifact (#380, B4).

    `run` is the latest_drift_run() entry (or None), `drift` the
    parsed upstream-drift.json (or None). Returns a dict whose `status` is "loaded", "stale" or
    "unavailable"; an unavailable snapshot carries only a `reason`, never partial numbers."""
    def unavailable(reason, run_url=None):
        return {"status": "unavailable", "reason": reason, "workflow_url": DRIFT_WORKFLOW, "run_url": run_url}
    if not isinstance(run, dict):
        return unavailable("no successful upstream-drift run on main was found")
    run_id, created, measured = run.get("databaseId"), run.get("createdAt"), None
    if type(run_id) is int and run_id > 0 and isinstance(created, str):
        try:
            measured = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            pass
    if measured is None or measured.tzinfo is None:
        return unavailable("the latest upstream-drift run has no usable id or timestamp")
    run_url = f"{BASE}/actions/runs/{run_id}"  # constructed, not copied from the API response
    if not isinstance(drift, dict):
        return unavailable("the upstream-drift artifact is missing, expired or unreadable", run_url)
    counts = {key: drift.get(key) for key in ("ahead", "behind", "conflict_count")}
    commits = {key: drift.get(key) for key in ("main", "upstream")}
    if (any(type(value) is not int or value < 0 for value in counts.values())
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)
                   for value in commits.values())):
        return unavailable("the upstream-drift artifact does not have the expected fields", run_url)
    age_seconds = (now - measured).total_seconds()
    if age_seconds < -300:
        return unavailable("the upstream-drift run is dated in the future", run_url)
    age_days = max(age_seconds, 0) / 86400
    stale = age_days > DRIFT_MAX_AGE_DAYS
    return {"status": "stale" if stale else "loaded", "run_url": run_url, "workflow_url": DRIFT_WORKFLOW,
            "measured_at": measured.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "age_days": round(age_days, 1), "max_age_days": DRIFT_MAX_AGE_DAYS, **counts, **commits,
            "reason": (f"the latest measurement is {age_days:.1f} days old, older than {DRIFT_MAX_AGE_DAYS} days"
                       if stale else None)}


def drift_section(snapshot):
    """Render the drift snapshot; a missing or stale measurement is stated, never left blank."""
    if snapshot is None:
        return ""
    upstream = (f'<a href="https://github.com/{UPSTREAM}/tree/{UPSTREAM_BRANCH}">'
                f'<code>{UPSTREAM}:{UPSTREAM_BRANCH}</code></a>')
    run_url = snapshot.get("run_url")
    run_link = f'<a href="{run_url}">run {run_url.rsplit("/", 1)[1]}</a>' if run_url else None
    head = ('<section class="readiness" id="upstream-drift" aria-labelledby="upstream-drift-title">'
            '<div><p class="eyebrow">Upstream drift</p><h2 id="upstream-drift-title">Distance from linux-port</h2>'
            f'<a href="{DRIFT_WORKFLOW}">Weekly drift workflow →</a></div><div>')
    if snapshot["status"] == "unavailable":
        return (head + '<p class="drift-notice"><strong>Drift snapshot unavailable:</strong> '
                f'{escape(snapshot["reason"])}.' + (f' Latest run: {run_link}.' if run_link else '')
                + f' The distance from {upstream} is unknown for this build.</p></div></section>')
    notice = (f'<p class="drift-notice"><strong>Stale drift snapshot:</strong> {escape(snapshot["reason"])}. '
              'The weekly run may have failed; treat these numbers as historical.</p>'
              if snapshot["status"] == "stale" else "")
    return (head + notice
            + f'<p><code>main</code> is <strong>{snapshot["ahead"]} commits ahead</strong> and '
            f'<strong>{snapshot["behind"]} behind</strong> {upstream}; a merge would conflict on '
            f'<strong>{snapshot["conflict_count"]} paths</strong>.</p>'
            f'<p class="muted">Measured <time datetime="{snapshot["measured_at"]}">{snapshot["measured_at"]}</time> '
            f'at <code>{snapshot["main"][:8]}</code> against <code>{snapshot["upstream"][:8]}</code> by {run_link}. '
            'The run is report-only and changes nothing.</p></div></section>')


def team_page(roster, rows, commit, extras=(), queue_available=True):
    phases = "".join(f'<option value="{key}">{label}</option>' for key, label in PHASES.items())
    streams = "".join(f'<option value="{key}">{label}</option>' for key, label in STREAMS.items())
    count = len(roster["agents"]) + len(extras)
    display_phases = [displayed_phase(agent) for agent in roster["agents"]]
    display_phases += [QUEUE_STATE_PHASE.get(entry["state"], "coordination") for entry in extras]
    completed = display_phases.count("completed")
    reviewing = display_phases.count("awaiting-review")
    observed = escape(roster["observed_at"], quote=True)
    if queue_available:
        markers = "Markers use the latest available queue claim, falling back to the curated phase."
        totals = "filters and totals use the latest available queue claim, falling back to the curated phase."
    else:
        markers = escape(QUEUE_UNAVAILABLE)
        totals = "the queue snapshot was unavailable for this build, so filters and totals use the curated phase."
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="description" content="Who is doing what on agterm for Omarchy: a source-linked snapshot of responsibilities, reviews and the release gate.">
<title>Team room · agterm for Omarchy</title><link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="team.css"><script src="team.js" defer></script></head>
<body><a class="skip" href="#main">Skip to team room</a>
<header><a class="wordmark" href="./">agterm<span>/omarchy</span></a><nav aria-label="Primary"><a href="./">Project hub</a><a href="{BASE}/blob/main/packaging/linux/omarchy.md">Install guide ↗</a><a href="#roster">The roster</a><a href="{BASE}/issues/1">GitHub hub ↗</a></nav></header>
<main id="main"><section class="hero" aria-labelledby="title"><div class="hero-copy"><p class="eyebrow">Team room / 01</p>
<h1 id="title">Different agents.<br><span>Shared direction.</span></h1><p class="lede">Who is building, checking, reviewing and connecting the pieces of agterm for Omarchy.</p>
<p class="snapshot"><span class="snapshot-mark" aria-hidden="true">▣</span> Evidence snapshot <time datetime="{observed}">{observed}</time></p></div>
<aside class="terminal" aria-label="Snapshot overview"><div class="terminal-bar"><span aria-hidden="true">● ● ●</span> team / status</div><div class="terminal-body"><p class="command">snapshot / reported ownership</p><dl class="numbers"><div><dt>named contributors</dt><dd>{count:02}</dd></div><div><dt>review / decision handoffs</dt><dd>{reviewing:02}</dd></div><div><dt>completed reports</dt><dd>{completed:02}</dd></div></dl><p class="terminal-note">A record of reports.<br>No online-status guessing.</p></div></aside></section>
<section class="pipeline-section" aria-labelledby="pipeline-title"><div class="section-top"><p class="eyebrow">How the work moves</p><h2 id="pipeline-title">Every handoff leaves a trace.</h2></div>
<ol class="pipeline"><li><span class="step">01 / implement</span><strong>Make it portable</strong><p>CPU baseline, queue behavior, and this team page.</p></li><li><span class="step">02 / validate</span><strong>Test the actual bytes</strong><p>Hosted builds, native Wayland, and macOS core checks.</p></li><li><span class="step">03 / review</span><strong>Challenge the claims</strong><p>Read-only audits and source-linked findings.</p></li><li><span class="step">04 / publish</span><strong>Root owns the gate</strong><p>Review, merge, deployment and release decisions.</p></li></ol></section>
{current_validation()}
<section class="gate" aria-labelledby="gate-title"><div><p class="eyebrow">Historical acceptance / passed at 6775c336</p><h2 id="gate-title">Historical candidate passed.<br>Current validation is separate.</h2><p>Trusted <a href="{BASE}/actions/runs/41357189891">run 41357189891</a> passed on ARM64 and x86_64 at <a href="{BASE}/commit/6775c33626925adc8a17e9ed7700f296f38d7688"><code>6775c336</code></a>. Both unchanged archives passed provenance, checksum and architecture validation. Root then ran that ARM64 archive (SHA256 prefix <code>2b629e40</code>) on native Omarchy: <strong>7 ordinary and 9 Live checks passed</strong>, with shell PID continuity across restart, positive-control cleanup and preservation of all three protected process identities and the installed app. <a href="{BASE}/issues/185#issuecomment-6565317571">Read the native execution report</a> and <a href="{BASE}/tree/48e70b33651ca8855f8cef5bd57f50afa67ed291/docs/evidence/omarchy-6775c336-2026-11-01">checksummed evidence</a>.</p><p>These runs used software rendering, a process-local zsh fixture and an external private-runtime adapter. Hardware rendering, default-bash Live support and native x86_64 acceptance remain unverified. WebKit checks establish load state, not pixel rendering. The shared generated-config path collision is reproduced in <a href="{BASE}/issues/124">#124</a>. The active application fix is <a href="{BASE}/issues/192">#192</a> / <a href="{BASE}/pull/195">PR #195</a>; <a href="{BASE}/issues/126">#126</a> retains the separate inert proposal. Follow <a href="{BASE}/issues/193">the resumed implementation and review waves</a> and <a href="{BASE}/issues/199">the additional native probe</a> for current owners, findings and gates. Test isolation did not change the accepted application source.</p><p>The accepted source is integrated on <code>main</code> through <a href="{BASE}/pull/6">PR #6</a> at <a href="{BASE}/commit/c88a5b641795a75ebbc0c4e2da1f85f548fb059f"><code>c88a5b64</code></a>. <a href="{BASE}/issues/17">Prerelease publication</a> remains separate; no application release was published in this acceptance snapshot. The evidence review in <a href="{BASE}/pull/189">PR #189</a> uses the shared account and is not independent-account approval.</p></div>
<div class="evidence-stack"><article><span class="proof good">Passed / exact native ARM64 archive</span><h3>7 ordinary + 9 Live checks</h3><p>Source <code>6775c336</code>: unchanged app and harness, restart continuity, sentinel and separate process cleanup, payload immutability and preservation of existing processes and files. Bundled zmx runs without the earlier SIGILL.</p><a href="{BASE}/pull/189">Inspect the exact-candidate evidence ↗</a></article><article><span class="proof good">Passed / both hosted architectures</span><h3>Trusted build + verified bytes</h3><p>ARM64 and x86_64 required CI gates and both original runtime archives passed validation at the same source. Only the ARM64 archive was executed in the native VM.</p><a href="{BASE}/actions/runs/41357189891">Open the trusted candidate run ↗</a></article><article><span class="proof historical">Historical / earlier hosted payload</span><h3>fd85 acceptance retained</h3><p>The earlier <code>fd85fc58</code> archive passed seven ordinary and nine Live native checks under its own recorded conditions. Its <a href="{BASE}/actions/runs/41335760930">trusted build</a>, <a href="{BASE}/issues/52#issuecomment-6562708234">execution report</a> and archive identities remain unchanged.</p><a href="{BASE}/tree/4d05d60c928c52b04df2ba51c1861cc1b90b8326/docs/evidence/omarchy-fd85fc58-2026-11-01">Inspect the earlier hosted-payload evidence ↗</a></article><article><span class="proof historical">Historical / failed candidate</span><h3>ARM64 pane reconnect</h3><p>Earlier <code>355c82ef</code> failed the reconnect assertion; no ARM64 runtime archive was uploaded. The unchanged assertion passed in later diagnostic and trusted candidate runs. Its original cause remains unclassified.</p><a href="{BASE}/actions/runs/41243677540">Open the retained failed run ↗</a></article><article><span class="proof historical">Historical / different payload</span><h3>Persistent renderer remedy</h3><p>The native-built <code>5e37cb75</code> installation plus wrapper <code>8458e16d</code> passed seven ordinary Wayland checks and an actual desktop-entry launch. The older nine-check Live result is also retained with its own payload identity.</p><a href="{BASE}/issues/56#issuecomment-6560840863">Read the renderer report ↗</a> · <a href="{BASE}/pull/116">Evidence review</a> · <a href="{BASE}/tree/main/docs/evidence/omarchy-arm64-2026-10-29">Older Live evidence</a></article></div></section>
<section id="roster" aria-labelledby="roster-title"><div class="section-top"><p class="eyebrow">People-shaped responsibilities / agent-sized work</p><h2 id="roster-title">Who is doing what</h2><p class="roster-intro">Roles describe scope. {markers} Source discussions decide ownership.</p></div>
<div class="filters" hidden><label>Find a name, role or task<input id="search" type="search" placeholder="Try portability, review, root…"></label><label>Reported phase<select id="phase"><option value="">All phases</option>{phases}</select></label><label>Workstream<select id="stream"><option value="">All workstreams</option>{streams}</select></label><button id="reset" type="button">Reset</button></div>
<p id="result-count" class="result-count" role="status" aria-live="polite">{count} contributors in this snapshot</p><p id="no-results" hidden>No matching reports. Reset the filters to see the full roster.</p>
<ul class="roster">{rows}</ul></section>
<section class="join-panel"><div><p class="eyebrow">There is room for another perspective</p><h2>Pick a task.<br>Bring back evidence.</h2></div><div><p>Read the latest thread, name your scope, and coordinate before editing. Review work can be valuable without claiming source ownership.</p><a class="button" href="{BASE}/issues/1">Join through the GitHub hub ↗</a> <a href="./#join">Copy the check-in format</a></div></section>
<aside class="snapshot-notes" aria-label="Queue and feature inventory"><strong>Requests and evidence have separate counts.</strong><p>The <a href="{BASE}/pull/121">bounded queue foundation</a> supports planning the 500-agent request; <strong>500 agents have not been launched</strong>. The <a href="{BASE}/blob/67e2d3bccc6e93b7844c1c4188bdbf63c968efed/packaging/linux/github-feature-evidence.md">verified inventory snapshot</a> records 72 proven, 45 pending and 4 unavailable in the stated scopes across 121 rows. This is not exhaustive account-wide feature coverage.</p></aside>
<aside class="snapshot-notes" aria-label="How to read this snapshot"><strong>Read the timestamp, not an implied heartbeat.</strong><p>Reported state is not proof of present liveness. Each card identifies its GitHub account. Names sharing an account are not separate GitHub identities. A different account alone does not establish independent approval. An open issue can contain completed historical work. Assignment, acknowledgment and review are distinct. Roles and historical reports are curated; {totals} Queue snapshots are not live polling; follow the linked source before acting. Native x86_64 Omarchy runtime acceptance remains unverified.</p></aside>
</main><footer><a class="wordmark" href="./">agterm<span>/omarchy</span></a><p>Source <a href="{BASE}/commit/{commit}">{commit[:8]}</a> · <a href="status.json">Machine-readable snapshot</a> · <a href="{BASE}/issues/53">Page task #53</a></p></footer></body></html>'''


def write_site(destination, pages, commit, limit=DEFAULT_TASK_LIMIT, roster=None, queue=None, queue_unavailable=False,
               drift=None):
    """`queue_unavailable`: a queue snapshot was requested but could not be read; say so on the page.
    `drift`: a drift_snapshot() result, or None when no drift snapshot was requested."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise ValueError("commit must be a hexadecimal Git commit ID")
    if limit not in TASK_LIMITS:
        raise ValueError("task limit must be 10, 25, 50, or 100")
    tasks = tasks_from_pages(pages, limit)
    roster = roster_snapshot(roster if roster is not None else
                             json.loads(Path(__file__).with_name("roster.json").read_text()))
    generated_at = datetime.now(timezone.utc)
    queue_note, extras, unverified = None, [], 0
    if queue is not None:
        agents, extras, queue_note, unverified = queue_overlay(roster, queue, generated_at)
        roster = {**roster, "agents": agents}
    roster_rows = "\n".join(
        f'<li class="roster-card" data-phase="{displayed_phase(agent)}" data-stream="{agent["workstream"]}">'
        f'<p class="stream">{STREAMS[agent["workstream"]]} / {escape(agent["role"])}</p>'
        f'<div class="roster-heading"><h3>{escape(agent["name"])}</h3>'
        f'<span class="phase">{PHASES[displayed_phase(agent)]}</span></div>'
        f'<p><a href="{agent["task_url"]}">{"PR" if agent["task_kind"] == "pull" else "Task"} '
        f'#{agent["task"]}</a> · Curated reported state: '
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
    if unverified:
        shown = "99+" if unverified > 99 else str(unverified)  # status.json keeps the true count
        roster_rows = (f'<li class="queue-note">{shown} unverified check-in(s) (not a collaborator) are '
                       'listed in the queue report, not as cards here.</li>\n' + roster_rows)
    if queue_note:
        roster_rows = f'<li class="queue-note">{escape(queue_note)}</li>\n' + roster_rows
    if queue is None and queue_unavailable:
        roster_rows = f'<li class="queue-note queue-unavailable">{escape(QUEUE_UNAVAILABLE)}</li>\n' + roster_rows
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
<header><a class="brand" href="./">agterm <span>/ Omarchy</span></a><nav aria-label="Primary"><a href="team.html">Team room</a><a href="{BASE}/blob/main/packaging/linux/omarchy.md">Install guide ↗</a><a href="#join">Join</a><a href="#tasks">Tasks</a><a href="{BASE}">Repository ↗</a></nav></header>
<main id="main"><section class="hero"><p class="eyebrow">A Linux terminal port, built in public</p>
<h1>One place to work.<br>One record of progress.</h1>
<p class="lead">Bringing agterm’s workspace and session model to Omarchy, with native ARM64 and x86_64 builds and real Hyprland testing.</p>
<div class="actions"><a class="button" href="{BASE}/issues/1">Open the orchestration hub</a><a href="{BASE}/pull/6">Review the port →</a></div></section>
{current_validation()}
{drift_section(drift)}
<section class="readiness" aria-labelledby="readiness-title"><div><p class="eyebrow">Readiness</p><h2 id="readiness-title">Historical candidate verified</h2></div><div><p>This is a development port, not a verified stable release. Trusted <a href="{BASE}/actions/runs/41357189891">run 41357189891</a> passed both hosted architectures at <a href="{BASE}/commit/6775c33626925adc8a17e9ed7700f296f38d7688"><code>6775c336</code></a>; both original archives passed provenance, checksum and architecture validation. Root ran that unchanged ARM64 archive (SHA256 prefix <code>2b629e40</code>) on native Omarchy: <strong>7 ordinary and 9 Live checks passed</strong>, including shell PID continuity across frontend restart, sentinel and separate process cleanup, payload immutability and preservation of all three protected process identities and the installed app. <a href="{BASE}/issues/185#issuecomment-6565317571">The execution report</a> and <a href="{BASE}/tree/48e70b33651ca8855f8cef5bd57f50afa67ed291/docs/evidence/omarchy-6775c336-2026-11-01">checksummed evidence</a> bind the exact bytes and conditions.</p><p>The native runs used software rendering, a process-local zsh fixture and an external private-runtime adapter; the actual account remained bash. Hardware rendering, default-bash Live support and native x86_64 acceptance remain unverified. WebKit checks establish load state, not pixel rendering. The separately reproduced shared generated-config path collision is tracked in <a href="{BASE}/issues/124">#124</a> and its active fix in <a href="{BASE}/issues/192">#192</a> / <a href="{BASE}/pull/195">PR #195</a>; <a href="{BASE}/issues/126">#126</a> retains the separate inert proposal. Follow <a href="{BASE}/issues/193">the resumed implementation and review waves</a> and <a href="{BASE}/issues/199">the additional native probe</a> for current owners, findings and gates. Test isolation did not change the accepted application source. The accepted source is integrated on <code>main</code> through <a href="{BASE}/pull/6">PR #6</a> at <a href="{BASE}/commit/c88a5b641795a75ebbc0c4e2da1f85f548fb059f"><code>c88a5b64</code></a>; <a href="{BASE}/issues/17">prerelease publication</a> remains pending. No application release was published in this acceptance snapshot.</p><p>Historical evidence is retained: the earlier <code>fd85fc58</code> archive passed seven ordinary and nine Live checks, bound to <a href="{BASE}/actions/runs/41335760930">run 41335760930</a>, its <a href="{BASE}/issues/52#issuecomment-6562708234">execution report</a> and <a href="{BASE}/tree/4d05d60c928c52b04df2ba51c1861cc1b90b8326/docs/evidence/omarchy-fd85fc58-2026-11-01">unchanged evidence bundle</a>. Separately, <a href="{BASE}/actions/runs/41243677540">run 41243677540</a> at <code>355c82ef</code> failed ARM64 reconnect with no ARM64 runtime archive; its cause remains unclassified although the unchanged assertion passed later. The older native-built <code>5e37cb75</code> installation plus wrapper <code>8458e16d</code> passed seven ordinary Wayland checks and an actual desktop-entry launch (<a href="{BASE}/issues/56#issuecomment-6560840863">report</a>, <a href="{BASE}/pull/116">evidence review</a>); its nine-check Live evidence has a separate payload identity. The shared Linux baseline is <strong>agterm v0.35.1</strong>; this documentation deployment is not another runtime test.</p><a href="{BASE}/pull/189">Exact-candidate evidence review</a> · <a href="{BASE}/tree/main/docs/evidence/omarchy-arm64-2026-10-29">Older native Live evidence</a> · <a href="{BASE}/issues/17">Track the prerelease</a></div></section>
<section id="team" aria-labelledby="team-title"><p class="eyebrow">Inside the team room</p><h2 id="team-title">Who is doing what</h2><p>{len(roster['agents']) + len(extras)} named contributors. Implementation, validation, review and integration, with responsibilities and source-linked handoffs.</p><p class="muted">Evidence snapshot: {escape(roster['observed_at'])}. Reported work is not proof of present liveness.</p><p>The <a href="{BASE}/pull/121">bounded queue foundation</a> supports the 500-agent request; <strong>500 agents have not been launched</strong>.</p><a class="button" href="team.html">Explore the team room →</a></section>
<section id="join" class="join"><div><p class="eyebrow">Contribute as a person or an agent</p><h2>Claim a task.<br>Leave evidence.</h2><p>GitHub issues are the task queue and communication record. Start in the hub, identify your execution environment, then claim an issue and the files you own before editing.</p></div><div><pre><code>Agent: &lt;name&gt;
Task: #&lt;number&gt;
Owned paths: &lt;paths&gt;
State: working
Connected: &lt;environment / runner&gt;
Evidence: &lt;test or artifact&gt;
Next: &lt;concrete action&gt;</code></pre><p>Post progress, blockers, test commands, and handoffs on the task issue. Link pull requests and artifacts. A shared GitHub login is not an independent reviewer. Keep credentials and private terminal content out of public evidence.</p><a href="{BASE}/issues/1">Post your check-in →</a></div></section>
<section id="tasks"><div class="section-heading"><div><p class="eyebrow">Work queue snapshot</p><h2>Follow the tasks</h2></div><a href="{BASE}/issues">Live issue queue ↗</a></div><ul class="tasks">{rows}</ul><p class="muted">Snapshot generated {escape(generated)}. Issue status can change after this page is built; the linked issue is authoritative.</p></section>
<section class="evidence" aria-labelledby="evidence-title"><p class="eyebrow">Proof, not promises</p><h2 id="evidence-title">Inspect the evidence</h2><div class="cards"><a href="{BASE}/actions/workflows/ci.yml"><h3>Builds and checks ↗</h3><p>Hosted matrix results, logs, and downloadable build artifacts. Check the commit and architecture.</p></a><a href="{BASE}/tree/48e70b33651ca8855f8cef5bd57f50afa67ed291/docs/evidence/omarchy-6775c336-2026-11-01"><h3>Historical native candidate ↗</h3><p>Checksummed ARM64 ordinary/Live results, restart continuity, cleanup and execution conditions.</p></a><a href="{BASE}/blob/67e2d3bccc6e93b7844c1c4188bdbf63c968efed/packaging/linux/github-feature-evidence.md"><h3>GitHub feature inventory ↗</h3><p>72 proven, 45 pending and 4 unavailable in the stated scopes across 121 rows. The inventory is not exhaustive account-wide feature coverage.</p></a></div></section>
</main><footer><p>Built from <a href="{BASE}/commit/{commit}">{commit[:8]}</a> · <a href="status.json">Machine-readable snapshot</a></p><p>Based on <a href="https://github.com/melonamin/agterm-linux">melonamin/agterm-linux</a> and <a href="https://github.com/umputun/agterm">upstream agterm</a>. This site documents the Omarchy port.</p></footer></body></html>
'''
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.html").write_text(content)
    (destination / "team.html").write_text(
        team_page(roster, roster_rows, commit, extras, queue_available=not (queue is None and queue_unavailable)))
    shutil.copyfile(Path(__file__).with_name("style.css"), destination / "style.css")
    for asset in ("team.css", "team.js", "favicon.svg"):
        shutil.copyfile(Path(__file__).with_name(asset), destination / asset)
    (destination / ".nojekyll").touch()
    (destination / "status.json").write_text(json.dumps({
        "repository": REPOSITORY, "readiness": "work-in-progress", "upstream_baseline": "v0.35.1",
        "commit": commit, "generated_at": generated, "tasks": tasks, "roster": roster,
        "queue": None if queue is None else {"observed_at": queue["observed_at"], "note": queue_note,
                                            "queue_only": extras, "unverified_count": unverified},
        # "not-requested": built without --queue; "unavailable": requested but missing or unreadable.
        "queue_status": "loaded" if queue is not None else "unavailable" if queue_unavailable else "not-requested",
        # Same vocabulary plus "stale" (older than DRIFT_MAX_AGE_DAYS; the numbers are kept, flagged).
        "upstream_drift": drift,
        "upstream_drift_status": "not-requested" if drift is None else drift["status"],
    }, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--limit", type=int, choices=TASK_LIMITS, default=DEFAULT_TASK_LIMIT)
    parser.add_argument("--queue", type=Path, help="queue.json from scripts/github/queue.py (optional)")
    parser.add_argument("--drift-run", type=Path,
                        help="`gh run list --json databaseId,createdAt,event,headBranch` output for "
                             "upstream-drift.yml (optional)")
    parser.add_argument("--drift", type=Path, help="upstream-drift.json from that run's artifact")
    args = parser.parse_args()
    drift = None
    if args.drift_run is not None or args.drift is not None:
        # Either input may be missing (the fetch step is continue-on-error); drift_snapshot says which.
        def load(path):
            try:
                return json.loads(path.read_text()) if path is not None else None
            except (OSError, ValueError):
                return None
        drift = drift_snapshot(latest_drift_run(load(args.drift_run)), load(args.drift), datetime.now(timezone.utc))
        if drift["status"] != "loaded":
            print(f"warning: upstream drift {drift['status']}: {drift['reason']}")
    queue, unavailable = None, False
    if args.queue is not None:
        # Requested but missing or unreadable (the snapshot step is continue-on-error): build, but say so.
        try:
            queue = json.loads(args.queue.read_text())
            if not isinstance(queue, dict) or not isinstance(queue.get("observed_at"), str):
                raise ValueError("queue snapshot has no observed_at")
        except (OSError, ValueError) as error:
            print(f"warning: {QUEUE_UNAVAILABLE} ({type(error).__name__})")
            queue, unavailable = None, True
    write_site(args.output, json.loads(args.issues.read_text()), args.commit, args.limit, queue=queue,
               queue_unavailable=unavailable, drift=drift)


if __name__ == "__main__":
    main()
