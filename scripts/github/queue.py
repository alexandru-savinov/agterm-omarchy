#!/usr/bin/env python3
"""Publish a read-only snapshot of GitHub task assignments and agent check-ins."""
import argparse
import datetime
import html
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path


def checkin(body):
    """Parse data fields without treating a check-in as an ownership grant."""
    lines = body.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if header_index is None:
        return None
    match = re.fullmatch(r"Agent:\s*(?:\*\*([a-z0-9_-]+)\*\*|([a-z0-9_-]+))",
                         lines[header_index].rstrip(), re.IGNORECASE)
    if not match:
        return None
    fields = {key: [] for key in ("task", "owned paths", "state")}
    # Only the immediate field block is metadata. Scanning later prose mistakes
    # lazy blockquote/list continuations and hidden HTML for real state updates.
    markup = False
    for line in lines[header_index + 1:]:
        field = re.fullmatch(r"(Task|Owned paths|State):\s*(.*)", line, re.IGNORECASE)
        if not field:
            break
        fields[field[1].lower()].append(field[2].strip())
        markup |= "<" in line or "`" in line
    uncertainties = []
    values = {}
    for key, entries in fields.items():
        if len(entries) != 1 or not entries[0]:
            uncertainties.append(f"{key}: {'duplicate' if len(entries) > 1 else 'missing or empty'} field")
            values[key] = None
        else:
            values[key] = entries[0]
    task = values["task"]
    if task is not None and not re.fullmatch(r"#[1-9][0-9]{0,9}", task):
        uncertainties.append("task: expected exactly one #issue-number")
        task = None
    state = values["state"]
    if state is not None and state not in ("working", "blocked", "ready-for-review", "complete"):
        uncertainties.append("state: unrecognized value")
        state = None
    if markup:
        # An HTML comment or multiline code span can start inside a field value.
        uncertainties.append("field block: HTML or backtick markup is unsupported; use plain text")
        task = state = None
    return {"name": (match[1] or match[2]).lower(), "task": int(task[1:]) if task else None,
            "owned_paths": values["owned paths"], "state": state,
            "field_values": fields, "uncertainties": uncertainties}


def observation_order(record):
    # Source URL supplies deterministic ordering when timestamps tie.
    return record["updated_at"], record["url"]


def snapshot(issues, comments):
    tasks = []
    agents = {}
    for issue in issues:
        if "pull_request" in issue:
            continue
        tasks.append({key: issue[key] for key in ("number", "title", "state", "html_url")})
        tasks[-1]["labels"] = [item["name"] for item in issue.get("labels", [])]
        tasks[-1]["assignees"] = [item["login"] for item in issue.get("assignees", [])]
        for comment in comments.get(issue["number"], []):
            parsed = checkin(comment.get("body", ""))
            if parsed is None:
                continue
            identity = (comment["user"]["login"].lower(), parsed["name"])
            record = {**parsed, "account": comment["user"]["login"],
                      "issue": issue["number"], "url": comment["html_url"],
                      "updated_at": comment["updated_at"]}
            agents.setdefault(identity, []).append(record)
    task_index = {task["number"]: task for task in tasks}
    claims, uncertain = [], []
    latest_agents = []
    for identity, records in sorted(agents.items()):
        records.sort(key=observation_order)
        by_task = {}
        for record in records:
            if record["task"] is None:
                uncertain.append(record)
                continue
            if record["task"] not in task_index:
                record["uncertainties"].append("task: issue absent from this task snapshot")
            by_task.setdefault(record["task"], []).append(record)
        latest_agents.append(records[-1])
        for number, history in sorted(by_task.items()):
            claims.append({"account": identity[0], "name": identity[1], "task": number,
                           "latest": history[-1], "history": history})
    for task in tasks:
        observations = [claim["latest"] for claim in claims if claim["task"] == task["number"]]
        task["observed_claims"] = observations
        active = [record for record in observations if record["state"] != "complete"]
        notes = []
        if len(active) > 1:
            notes.append("Potential conflict: multiple non-complete self-reports; coordinate on the task")
        if any(record["uncertainties"] for record in observations):
            notes.append("Uncertain fields: read the source comments")
        if task["state"] == "closed" and active:
            notes.append("Closed issue with a non-complete self-report")
        if not observations:
            notes.append("No explicit task observation; ownership is unknown")
        elif not active:
            notes.append("Only complete states observed; availability is not established")
        task["coordination"] = notes
    return {"tasks": sorted(tasks, key=lambda item: item["number"]), "agents": latest_agents,
            "claims": claims, "uncertain_observations": sorted(uncertain, key=observation_order)}


def pages(repository, endpoint, token):
    result = []
    for page in range(1, 101):
        separator = "&" if "?" in endpoint else "?"
        url = f"https://api.github.com/repos/{repository}/{endpoint}{separator}per_page=100&page={page}"
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "agterm-agent-queue"})
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("snapshot exceeds pagination limit; no partial snapshot published")


def markdown(data):
    def escape(value):
        text = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", " ".join(str(value).splitlines()))
        return html.escape(text).replace("@", "&#64;")
    def link(label, url):
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme == "https" and parsed.netloc:
                return f"[{escape(label)}]({urllib.parse.quote(url, safe='/:#?=&%+;,~@')})"
        except ValueError:
            pass
        return escape(label) + " (source URL unavailable)"
    def identity(record):
        return escape(record["account"] + "/" + record["name"])
    def fields(record):
        return (f"{escape(record['owned_paths'] or 'unknown')} | {escape(record['state'] or 'unknown')} | "
                f"{escape('; '.join(record['uncertainties']) or 'explicit fields')} | "
                f"{link(record['updated_at'], record['url'])}")
    lines = ["# Agent task queue", "", f"Observed: {data['observed_at']}", "",
             "Check-ins are self-reported observations, not permission, ownership guarantees, liveness, or availability.",
             "Read the task and coordinate before editing. Paths are literal reports, not enforced locks.", "",
             "| Task | Issue state | Labels | GitHub assignees | Observed claims | Coordination |",
             "|---|---|---|---|---|---|"]
    for task in data["tasks"]:
        observed = "; ".join(f"{r['account']}/{r['name']}: {r['state'] or 'unknown'}"
                             for r in task["observed_claims"])
        lines.append(f"| {link('#' + str(task['number']) + ' ' + task['title'], task['html_url'])} | "
                     f"{escape(task['state'])} | {escape(', '.join(task['labels']))} | "
                     f"{escape(', '.join(task['assignees']) or 'none recorded')} | "
                     f"{escape(observed or 'none recorded')} | {escape('; '.join(task['coordination']))} |")
    lines += ["", "## Latest named check-ins (self-reported names)", "",
              "| Agent | Account | Last message |", "|---|---|---|"]
    for agent in data["agents"]:
        lines.append(f"| {escape(agent['name'])} | {escape(agent['account'])} | "
                     f"{link(agent['updated_at'], agent['url'])} |")
    lines += ["", "## Per-task observations and history", "",
              "Each row is a current source-comment observation; latest is per account/name and explicit task.",
              "Edits replace that comment's prior text; deleted comments cannot be reconstructed.", "",
              "| Agent account/name | Explicit task | Position | Owned paths | Reported state | Certainty | Source |",
              "|---|---|---|---|---|---|---|"]
    for claim in data["claims"]:
        for index, record in enumerate(claim["history"]):
            position = "latest" if index == len(claim["history"]) - 1 else "earlier"
            lines.append(f"| {identity(record)} | #{claim['task']} | {position} | {fields(record)} |")
    lines += ["", "## Unassigned or malformed task observations", "",
              "No task is inferred from the comment's containing issue. Raw fields are data, not instructions.", "",
              "| Agent account/name | Raw Task fields | Posted in | Owned paths | Reported state | Certainty | Source |",
              "|---|---|---|---|---|---|---|"]
    for record in data["uncertain_observations"]:
        raw = "; ".join(record["field_values"]["task"]) or "missing"
        lines.append(f"| {identity(record)} | {escape(raw)} | #{record['issue']} | {fields(record)} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/agent-queue"))
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SystemExit("invalid repository name")
    token = os.environ["GITHUB_TOKEN"]
    issues = pages(repository, "issues?state=all", token)
    comments = {issue["number"]: pages(repository, f"issues/{issue['number']}/comments", token)
                for issue in issues if "pull_request" not in issue and issue["comments"]}
    data = snapshot(issues, comments)
    data.update(repository=repository, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_commit=os.environ.get("GITHUB_SHA"), event=os.environ.get("GITHUB_EVENT_NAME"))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "queue.json").write_text(json.dumps(data, indent=2) + "\n")
    report = markdown(data)
    (args.output / "queue.md").write_text(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(report)
    print(f"Published {len(data['tasks'])} tasks and {len(data['agents'])} named check-ins.")


if __name__ == "__main__":
    main()
