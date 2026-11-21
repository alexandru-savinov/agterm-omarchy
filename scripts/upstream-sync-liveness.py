#!/usr/bin/env python3
"""Reconcile one issue for stale upstream-sync schedule deliveries."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY = "alexandru-savinov/agterm-omarchy"
LABEL = "upstream-sync-schedule"
TITLE = "Upstream sync schedule liveness alarm"
STALE_AGE = timedelta(hours=12)


def schedule_status(listing, now):
    """Return (stale, last scheduled run), ignoring whether that run actually succeeded."""
    runs = listing["workflow_runs"]
    if not isinstance(runs, list):
        raise ValueError("invalid workflow run listing")
    if not runs:
        if listing["total_count"] != 0:
            raise ValueError("incomplete workflow run listing")
        return True, None
    run = runs[0]  # The workflow runs endpoint lists newest creations first.
    if (run["event"] != "schedule" or run["head_branch"] != "main"
            or type(run["id"]) is not int or run["id"] <= 0):
        raise ValueError("unexpected workflow run identity")
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    if created.tzinfo is None or created > now:
        raise ValueError("invalid workflow run creation time")
    return now - created >= STALE_AGE, (run["id"], created.astimezone(timezone.utc))


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def reconcile(client, now):
    listing = client("api", f"repos/{REPOSITORY}/actions/workflows/upstream-sync.yml/runs"
                            "?branch=main&event=schedule&per_page=1")
    stale, run = schedule_status(json.loads(listing), now)
    if run:
        run_id, created = run
        last = f"Last scheduled run was created at {created.isoformat()} ([run {run_id}]"
        last += f"(https://github.com/{REPOSITORY}/actions/runs/{run_id}))."
    else:
        last = "GitHub lists no scheduled upstream sync run on main."

    # List by label directly. The search index can lag behind a newly created issue.
    issues = json.loads(client("issue", "list", "--state", "all", "--label", LABEL,
                               "--limit", "1000", "--json", "number,state"))
    if not isinstance(issues, list):
        raise ValueError("invalid schedule alarm issue listing")
    if len(issues) > 1:
        raise ValueError("multiple schedule alarm issues exist; reconcile them before retrying")
    issue = issues[0] if issues else None
    if issue and (type(issue["number"]) is not int or issue["number"] <= 0
                  or issue["state"] not in ("OPEN", "CLOSED")):
        raise ValueError("invalid schedule alarm issue")
    if not stale:
        if issue and issue["state"] == "OPEN":
            client("issue", "close", str(issue["number"]), "--comment", f"Schedule delivery recovered. {last}")
        return f"Schedule is live. {last}"

    body = (
        "No upstream-sync schedule event has created a workflow run on main in the last 12 hours.\n\n"
        f"{last}\n\n"
        f"Checked at {now.isoformat()}. This alarm checks delivery, regardless of the run conclusion. "
        "It checks on push to main as well as on its own schedule."
    )
    if issue:
        number = str(issue["number"])
        client("issue", "edit", number, "--title", TITLE, "--body", body)
        if issue["state"] == "CLOSED":
            client("issue", "reopen", number)
        link = f"https://github.com/{REPOSITORY}/issues/{number}"
    else:
        try:
            client("api", f"repos/{REPOSITORY}/labels", "-f", f"name={LABEL}", "-f", "color=d93f0b",
                   "-f", "description=Upstream sync schedule delivery liveness")
        except subprocess.CalledProcessError as error:
            if "already_exists" not in ((error.stdout or "") + (error.stderr or "")):
                raise
        link = client("issue", "create", "--title", TITLE, "--label", LABEL, "--body", body).strip()
    return f"Schedule is stale. {last} Alarm: {link}"


def main():
    if os.environ.get("GH_REPO") != REPOSITORY or os.environ.get("GITHUB_REF") != "refs/heads/main":
        sys.exit("upstream sync liveness runs only on collaboration main")
    try:
        message = reconcile(gh, datetime.now(timezone.utc))
        print(message)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary).open("a", encoding="utf-8") as output:
                output.write(f"{message}\n")
    except (ValueError, TypeError, KeyError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        sys.exit(f"upstream sync liveness: {detail}")


if __name__ == "__main__":
    main()
