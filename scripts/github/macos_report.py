#!/usr/bin/env python3
"""Apply the advisory macOS result using GitHub's current PR and job metadata."""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

WORKFLOW = ".github/workflows/omarchy-macos.yml"
JOB = "Hosted macOS checks"
LABEL = "macos-broken"
REPO = "alexandru-savinov/agterm-omarchy"
VERDICTS = {"success", "failure", "timed_out"}


def request(path, method="GET", data=None, pages=False):
    command = ["gh", "api", f"repos/{REPO}/{path}", "--method", method]
    if pages:
        command += ["--paginate", "--slurp"]
    if data is not None:
        command += ["--input", "-"]
    result = subprocess.check_output(command, input=json.dumps(data).encode() if data is not None else None)
    return json.loads(result) if result else None


def eligible_run(run):
    return (run["event"] == "pull_request" and run["path"] == WORKFLOW
            and run["repository"]["full_name"] == REPO and run["status"] == "completed"
            and bool(run.get("head_repository")))


def current_pr(run, prs):
    linked = {p["number"] for p in run.get("pull_requests", [])}
    candidates = []
    for pr in prs:
        head = pr["head"]
        repo = head.get("repo") or {}
        if (pr["state"] == "open" and pr["base"]["repo"]["full_name"] == REPO
                and (not linked or pr["number"] in linked)
                and repo.get("id") == run["head_repository"]["id"]
                and head["ref"] == run["head_branch"] and head["sha"] == run["head_sha"]):
            candidates.append(pr)
    # Fork runs can omit pull_requests. Do not guess if the head matches multiple PRs.
    return candidates[0] if len(candidates) == 1 else None


def verdict(run, api):
    jobs_path = f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
    jobs = [job for page in api(jobs_path, pages=True) for job in page["jobs"] if job["name"] == JOB]
    if len(jobs) == 1 and jobs[0]["status"] == "completed" and jobs[0]["conclusion"] in VERDICTS:
        return jobs[0]
    return None


def report(run, api=request):
    if not eligible_run(run):
        return
    latest = api(f"actions/runs/{run['id']}")
    if not eligible_run(latest) or latest["run_attempt"] != run["run_attempt"]:
        return
    run = latest
    job = verdict(run, api)
    if job is None:
        return
    repo = run["head_repository"]
    head = quote(f"{repo['owner']['login']}:{run['head_branch']}", safe="")
    pages = api(f"pulls?state=open&head={head}&per_page=100", pages=True)
    pr = current_pr(run, (pr for page in pages for pr in page))
    if pr is None:
        return

    runs_path = (f"actions/workflows/{run['workflow_id']}/runs?event=pull_request"
                 f"&head_sha={run['head_sha']}&per_page=100")
    for page in api(runs_path, pages=True):
        for later in page["workflow_runs"]:
            if (later["run_number"] > run["run_number"] and eligible_run(later)
                    and current_pr(later, [pr]) is not None and verdict(later, api) is not None):
                return

    # Recheck the open head after the paginated reads before making any write.
    pr = current_pr(run, [api(f"pulls/{pr['number']}")])
    if pr is None:
        return
    path = f"issues/{pr['number']}"
    labelled = any(label["name"] == LABEL for label in pr["labels"])
    if job["conclusion"] == "success":
        if labelled:
            api(f"{path}/labels/{LABEL}", method="DELETE")
        return
    if not labelled:
        labels = api("labels?per_page=100", pages=True)
        if not any(label["name"] == LABEL for page in labels for label in page):
            api("labels", method="POST", data={"name": LABEL, "color": "d93f0b",
                                               "description": "Advisory hosted macOS checks failed or timed out"})
        api(f"{path}/labels", method="POST", data={"labels": [LABEL]})
    if job["conclusion"] == "timed_out":
        status = "timed out"
    elif job.get("steps"):
        status = "failed"
    else:
        status = "did not run"
    url = f"https://github.com/{REPO}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"
    api(f"{path}/comments", method="POST", data={"body": f"Hosted macOS checks {status}. [Run details]({url})"})


if __name__ == "__main__":
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    report(event["workflow_run"])
