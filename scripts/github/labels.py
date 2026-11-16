#!/usr/bin/env python3
"""Mirror the latest verified check-in on each task issue into `state:*` and `agent:*` labels.

Reads the queue.json that queue.py publishes, so the parser, the collaborator check and the
staleness rule are the queue's own. The labels are a read-only view of that snapshot: they make
"what is blocked?" one `gh issue list --label state:blocked`. They never grant ownership.
"""
import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATES = ("working", "blocked", "ready-for-review", "complete")
STATE_PREFIX, AGENT_PREFIX = "state:", "agent:"
MAX_LABEL = 50  # GitHub's limit on a label name
NAME = re.compile(r"[a-z0-9_-]+", re.ASCII)
COLORS = {STATE_PREFIX: "1d76db", AGENT_PREFIX: "bfd4f2"}


def managed(label):
    return label.startswith((STATE_PREFIX, AGENT_PREFIX))


def desired(task):
    """The labels this task should carry, or None when no verified claim is visible.

    None means "leave the labels alone": the queue only loads recent comments, so an old
    `blocked` claim can be invisible, and an absent claim must not erase a visible label."""
    claims = [record for record in task.get("observed_claims", [])
              if record.get("verified") and record.get("state") in STATES]
    if not claims:
        return None
    newest = max(claims, key=lambda record: (record["updated_at"], record["url"]))
    labels = {STATE_PREFIX + newest["state"]}
    for record in claims:
        name = str(record.get("name") or "").lower()
        # A stale `working` claim and a finished one are not "who has this now".
        if record["state"] == "complete" or record.get("stale"):
            continue
        if NAME.fullmatch(name) and len(AGENT_PREFIX + name) <= MAX_LABEL:
            labels.add(AGENT_PREFIX + name)
    return labels


def plan(task):
    """(add, remove) label sets for one task, from the snapshot's own label list."""
    want = desired(task)
    if want is None:
        return set(), set()
    current = {label for label in task.get("labels", []) if managed(label)}
    return want - current, current - want


def plan_all(data):
    return {task["number"]: plan(task) for task in data["tasks"]
            if any(plan(task))}


def request(method, url, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "agterm-claim-labels",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status


def apply(repository, token, changes, send=request):
    base = f"https://api.github.com/repos/{repository}"
    for number, (add, remove) in sorted(changes.items()):
        for label in sorted(add):
            try:
                send("POST", f"{base}/labels", token,
                     {"name": label, "color": COLORS[label.split(":")[0] + ":"]})
            except urllib.error.HTTPError as error:
                if error.code != 422:  # 422: the label already exists
                    raise
        if add:
            send("POST", f"{base}/issues/{number}/labels", token, {"labels": sorted(add)})
        for label in sorted(remove):
            try:
                send("DELETE", f"{base}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}", token)
            except urllib.error.HTTPError as error:
                if error.code != 404:  # 404: someone removed it first
                    raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=Path("artifacts/agent-queue/queue.json"))
    parser.add_argument("--apply", action="store_true", help="write labels; without it, only print the plan")
    args = parser.parse_args()
    changes = plan_all(json.loads(args.queue.read_text()))
    for number, (add, remove) in sorted(changes.items()):
        print(f"#{number}: +{sorted(add)} -{sorted(remove)}")
    print(f"{len(changes)} issues would change" if not args.apply else f"{len(changes)} issues updated")
    if args.apply and changes:
        repository = os.environ["GITHUB_REPOSITORY"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise SystemExit("invalid repository name")
        apply(repository, os.environ["GITHUB_TOKEN"], changes)


if __name__ == "__main__":
    main()
