#!/usr/bin/env python3
"""Publish a read-only snapshot of GitHub task assignments and agent check-ins."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import urllib.request


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
            header = next((line.strip() for line in comment.get("body", "").splitlines() if line.strip()), "")
            match = re.fullmatch(r"Agent:\s*(?:\*\*([a-z0-9_-]+)\*\*|([a-z0-9_-]+))", header, re.I)
            if not match:
                continue
            name = (match.group(1) or match.group(2)).lower()
            identity = (comment["user"]["login"].lower(), name)
            record = {"name": name, "account": comment["user"]["login"],
                      "issue": issue["number"], "url": comment["html_url"],
                      "updated_at": comment["updated_at"]}
            if identity not in agents or record["updated_at"] > agents[identity]["updated_at"]:
                agents[identity] = record
    return {"tasks": sorted(tasks, key=lambda item: item["number"]),
            "agents": sorted(agents.values(), key=lambda item: (item["account"].lower(), item["name"]))}


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
        return str(value).replace("\n", " ").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
    lines = ["# Agent task queue", "", f"Observed: {data['observed_at']}", "",
             "Check-ins prove a message was posted, not that an agent is still running.", "",
             "| Task | State | Labels |", "|---|---|---|"]
    for task in data["tasks"]:
        lines.append(f"| [#{task['number']} {escape(task['title'])}]({task['html_url']}) | "
                     f"{task['state']} | {escape(', '.join(task['labels']))} |")
    lines += ["", "## Latest named check-ins (self-reported names)", "",
              "| Agent | Account | Last message |", "|---|---|---|"]
    for agent in data["agents"]:
        lines.append(f"| {escape(agent['name'])} | {escape(agent['account'])} | "
                     f"[{agent['updated_at']}]({agent['url']}) |")
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
