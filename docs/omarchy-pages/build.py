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


def write_site(destination, pages, commit, limit=25):
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise ValueError("commit must be a hexadecimal Git commit ID")
    if limit not in (10, 25, 50):
        raise ValueError("task limit must be 10, 25, or 50")
    tasks = tasks_from_pages(pages, limit)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = "\n".join(
        f'<li><a href="{BASE}/issues/{task["number"]}"><span class="number">#{task["number"]}</span>'
        f'<span>{escape(task["title"])}</span></a><span class="state {task["state"]}">{task["state"]}</span></li>'
        for task in tasks
    ) or '<li>No tasks were included in this snapshot. Open the live issue queue below.</li>'
    content = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="description" content="Development hub, agent coordination, and readiness evidence for the agterm Omarchy port.">
<title>agterm for Omarchy · Development hub</title><link rel="stylesheet" href="style.css"></head>
<body><a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="./">agterm <span>/ Omarchy</span></a><nav aria-label="Primary"><a href="#join">Join</a><a href="#tasks">Tasks</a><a href="{BASE}">Repository ↗</a></nav></header>
<main id="main"><section class="hero"><p class="eyebrow">A Linux terminal port, built in public</p>
<h1>One place to work.<br>One record of progress.</h1>
<p class="lead">Bringing agterm’s workspace and session model to Omarchy, with native ARM64 and x86_64 builds and real Hyprland testing.</p>
<div class="actions"><a class="button" href="{BASE}/issues/1">Open the orchestration hub</a><a href="{BASE}/pull/6">Review the port →</a></div></section>
<section class="readiness" aria-labelledby="readiness-title"><div><p class="eyebrow">Readiness</p><h2 id="readiness-title">Work in progress</h2></div><div><p>This is a development port, not a verified stable release. Native installation, rendering, input, and overlay acceptance must be backed by test evidence before that status changes.</p><p>The shared Linux frontend baseline is <strong>agterm v0.35.1</strong>. A green documentation deployment does not establish terminal readiness.</p><a href="{BASE}/issues/5">Read the native acceptance evidence →</a></div></section>
<section id="join" class="join"><div><p class="eyebrow">Contribute as a person or an agent</p><h2>Claim a task.<br>Leave evidence.</h2><p>GitHub issues are the task queue and communication record. Start in the hub, identify your execution environment, then claim an issue and the files you own before editing.</p></div><div><pre><code>Agent: &lt;name&gt;
Connected: &lt;environment / runner&gt;
Task: #&lt;number&gt; or available
Owned paths: &lt;paths&gt;
Next evidence: &lt;test or artifact&gt;</code></pre><p>Post progress, blockers, test commands, and handoffs on the task issue. Link pull requests and artifacts. A shared GitHub login is not an independent reviewer. Keep credentials and private terminal content out of public evidence.</p><a href="{BASE}/issues/1">Post your check-in →</a></div></section>
<section id="tasks"><div class="section-heading"><div><p class="eyebrow">Work queue snapshot</p><h2>Follow the tasks</h2></div><a href="{BASE}/issues">Live issue queue ↗</a></div><ul class="tasks">{rows}</ul><p class="muted">Snapshot generated {escape(generated)}. Issue status can change after this page is built; the linked issue is authoritative.</p></section>
<section class="evidence" aria-labelledby="evidence-title"><p class="eyebrow">Proof, not promises</p><h2 id="evidence-title">Inspect the evidence</h2><div class="cards"><a href="{BASE}/actions/workflows/ci.yml"><h3>Builds and checks ↗</h3><p>Hosted matrix results, logs, and downloadable build artifacts. Check the commit and architecture.</p></a><a href="{BASE}/issues/5"><h3>Native desktop ↗</h3><p>Omarchy VM installation, Wayland rendering, PTY input, and overlay acceptance.</p></a><a href="{BASE}/issues/4"><h3>GitHub feature inventory ↗</h3><p>Which capabilities have evidence, which are only enabled, and which remain unavailable.</p></a></div></section>
</main><footer><p>Built from <a href="{BASE}/commit/{commit}">{commit[:8]}</a> · <a href="status.json">Machine-readable snapshot</a></p><p>Based on <a href="https://github.com/melonamin/agterm-linux">melonamin/agterm-linux</a> and <a href="https://github.com/umputun/agterm">upstream agterm</a>. This site documents the Omarchy port.</p></footer></body></html>
'''
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.html").write_text(content)
    shutil.copyfile(Path(__file__).with_name("style.css"), destination / "style.css")
    (destination / ".nojekyll").touch()
    (destination / "status.json").write_text(json.dumps({
        "repository": REPOSITORY, "readiness": "work-in-progress", "upstream_baseline": "v0.35.1",
        "commit": commit, "generated_at": generated, "tasks": tasks,
    }, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--limit", type=int, choices=(10, 25, 50), default=25)
    args = parser.parse_args()
    write_site(args.output, json.loads(args.issues.read_text()), args.commit, args.limit)


if __name__ == "__main__":
    main()
