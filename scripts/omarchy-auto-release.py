#!/usr/bin/env python3
"""Plan an automatic Omarchy release, watch publication, or retract on failed release gates."""

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SPEC = importlib.util.spec_from_file_location(
    "promotion", Path(__file__).with_name("prepare-omarchy-prerelease.py"))
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)

MINIMUM_BASE = (0, 43, 0)
TAG = re.compile(r"omarchy-v(?P<base>(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2})\.(?P<serial>[1-9][0-9]*)")
WATCH_WINDOW = timedelta(hours=24)
INCIDENT = "release-incident"
RELEASES_PATH = "releases?per_page=100"
MAIN = "refs/heads/main"


def paused(value):
    return value.strip().lower() == "true"


def gh(path, *, method="GET", data=None, variables=False, pages=False):
    command = ["gh", "api", "-X", method, "-H", "X-GitHub-Api-Version: 2026-03-10",
               f"repos/{promotion.REPOSITORY}/{path}"]
    env = os.environ.copy()
    if variables:
        token = env.pop("SYNC_PAUSE_TOKEN", "")
        promotion.require(token, "SYNC_PAUSE_TOKEN needs Variables: write on this repository")
        env["GH_TOKEN"] = token
    if pages:
        command += ["--paginate", "--slurp"]
    kwargs = {}
    if data is not None:
        command += ["--input", "-"]
        kwargs["input"] = json.dumps(data)
    result = subprocess.run(command, text=True, check=True, capture_output=True, env=env, **kwargs)
    return json.loads(result.stdout) if result.stdout.strip() else None


def pause_variable():
    pages = gh("actions/variables?per_page=30", variables=True, pages=True)
    return next((var["value"] for page in pages for var in page["variables"]
                 if var["name"] == "SYNC_PAUSED"), None)


def releases():
    return [item for page in promotion.api(RELEASES_PATH, pages=True) for item in page
            if TAG.fullmatch(item["tag_name"])]


def next_version(base, tags):
    serials = [int(match["serial"]) for tag in tags
               if (match := TAG.fullmatch(tag)) and match["base"] == base]
    return f"{base}.{max(serials, default=0) + 1}"


def plan(event):
    if paused(os.environ.get("SYNC_PAUSED", "")):
        return None, "SYNC_PAUSED is true"
    source = event["workflow_run"]
    if (source["event"], source["head_branch"], source["conclusion"]) != ("push", "main", "success"):
        return None, "Expected a successful main push"
    run_id, sha, attempt = source["id"], source["head_sha"], source["run_attempt"]
    head = promotion.api("git/ref/heads/main")["object"]["sha"]
    if head != sha:
        return None, "A newer main commit superseded this CI run"
    run = promotion.api(f"actions/runs/{run_id}")
    if run["run_attempt"] != attempt or run["status"] != "completed":
        return None, "The triggering attempt is no longer current and completed"
    promotion.require(run["id"] == run_id and run["event"] == "push" and run["head_branch"] == "main",
                      "Not the requested main push run")
    promotion.validate_run(run, promotion.api("actions/workflows/ci.yml"), sha, head)
    base = promotion.source_base(promotion.release_module(), sha)
    if promotion.version_key(base) < MINIMUM_BASE:
        return None, "Wait for the v0.43.0 catch-up sync before automatic releases"
    items = releases()
    shipped = [item for item in items if not item["draft"]]
    if shipped:
        # Include retracted releases so fixing forward requires a new main commit.
        latest = max(shipped, key=lambda item: promotion.version_key(item["tag_name"].removeprefix("omarchy-v")))
        if promotion.api(f"compare/{latest['tag_name']}...{sha}")["status"] != "ahead":
            return None, f"No new main commit after {latest['tag_name']}"
    elif promotion.version_key(base) != MINIMUM_BASE:
        return None, "M4 starts the versioned release series at upstream v0.43.0"
    # A retraction, draft, or partially published release can leave a tag behind.
    refs = promotion.api("git/matching-refs/tags/omarchy-v?per_page=100", pages=True)
    tags = [item["ref"].removeprefix("refs/tags/") for page in refs for item in page]
    version = next_version(base, tags + [item["tag_name"] for item in items])
    return {"run_id": str(run_id), "sha": sha, "attempt": str(attempt), "version": version}, "Candidate selected"


def find_watch_tags(items, now):
    tags = []
    for item in items:
        if item["draft"] or item["prerelease"] or not item["published_at"]:
            continue
        published = datetime.fromisoformat(item["published_at"])
        if timedelta(0) <= now - published < WATCH_WINDOW:
            tags.append(item["tag_name"])
    return tags


def dispatch(candidate):
    if paused(pause_variable() or ""):
        print("SYNC_PAUSED is true; promotion skipped.")
        return ""
    response = gh("actions/workflows/prerelease-omarchy.yml/dispatches", method="POST", data={
        "ref": "main",
        "inputs": {"source_run": candidate["run_id"], "source_sha": candidate["sha"],
                   "source_attempt": candidate["attempt"], "version": candidate["version"], "dry_run": False},
    })
    run_id = response["workflow_run_id"]
    promotion.require(type(run_id) is int and run_id > 0, "Dispatch did not return a promotion run ID")
    print(f"Dispatched promotion {run_id}", flush=True)
    return wait_for_promotion(run_id, candidate)


def wait_for_promotion(run_id, candidate):
    deadline = time.monotonic() + 35 * 60
    failures = 0
    while time.monotonic() < deadline:
        try:
            run = gh(f"actions/runs/{run_id}")
        except subprocess.CalledProcessError:
            failures += 1
            if failures == 6:
                raise
            time.sleep(5 * 2 ** (failures - 1))
            continue
        failures = 0
        if run["status"] == "completed":
            promotion.require(run["conclusion"] == "success", f"Promotion run {run_id} failed")
            tag = f"omarchy-v{candidate['version']}"
            release = promotion.api(f"releases/tags/{tag}")
            ref = promotion.api(f"git/ref/tags/{tag}")
            promotion.require(not release["draft"] and not release["prerelease"] and
                              ref["object"]["sha"] == candidate["sha"], f"{tag} is not the expected stable release")
            return tag
        time.sleep(20)
    raise ValueError(f"Promotion run {run_id} is still active; scheduled watches discover published releases")


def set_pause():
    data = {"name": "SYNC_PAUSED", "value": "true"}
    if pause_variable() is None:
        try:
            gh("actions/variables", method="POST", data=data, variables=True)
            return
        except subprocess.CalledProcessError:
            # An operator may have created the variable after we read the list.
            if pause_variable() is None:
                raise
    gh("actions/variables/SYNC_PAUSED", method="PATCH", data=data, variables=True)


def record_incident(tag, reason, actions, evidence):
    title = f"Release incident: {tag}"
    # The watch serializes this operation per tag. Keep one incident even across retries.
    items = promotion.api(f"issues?labels={INCIDENT}&state=all&per_page=100", pages=True)
    if any(item["title"] == title for page in items for item in page if "pull_request" not in item):
        return
    if promotion.absent(f"labels/{INCIDENT}"):
        try:
            gh("labels", method="POST", data={"name": INCIDENT, "color": "B60205",
                                             "description": "Published Omarchy release failed its watch"})
        except subprocess.CalledProcessError:
            if promotion.absent(f"labels/{INCIDENT}"):
                raise
    body = (f"The post-release watch failed for [`{tag}`](https://github.com/"
            f"{promotion.REPOSITORY}/releases/tag/{tag}).\n\n"
            f"- Failure: {reason}\n"
            f"- Mark release as prerelease: {actions['retract']}\n"
            f"- Set `SYNC_PAUSED=true`: {actions['pause']}\n"
            f"- Evidence: {evidence}\n\n"
            "Investigate the failure and fix forward. Keep the release and tag for incident evidence.")
    issue = gh("issues", method="POST", data={"title": title, "body": body, "labels": [INCIDENT]})
    print(f"Incident: {issue['html_url']}")


def retract(tag, reason, evidence, dry_run):
    promotion.require(TAG.fullmatch(tag), "Expected a canonical versioned Omarchy tag")
    if dry_run:
        print(json.dumps({"dry_run": True, "tag": tag, "failure": reason,
                          "release": {"prerelease": True, "make_latest": "false"},
                          "repo_variable": {"SYNC_PAUSED": "true"}, "issue_label": INCIDENT}, indent=2))
        return

    actions = {}
    # Pause first to stop pending promotions. Retraction and the incident still run if pausing fails.
    for name, action in (("pause", set_pause), ("retract", lambda: mark_prerelease(tag))):
        try:
            action()
            actions[name] = "done"
        except (ValueError, subprocess.CalledProcessError) as error:
            actions[name] = "failed"
            report_error(error)
    record_incident(tag, reason, actions, evidence)
    promotion.require(all(result == "done" for result in actions.values()),
                      f"Incomplete incident response for {tag}. See the incident and retry failed jobs.")


def mark_prerelease(tag):
    release = promotion.api(f"releases/tags/{tag}")
    promotion.require(release["tag_name"] == tag and not release["draft"] and release["published_at"],
                      "Only an already published release can be retracted")
    if not release["prerelease"]:
        gh(f"releases/{int(release['id'])}", method="PATCH",
           data={"prerelease": True, "make_latest": "false"})


def report_error(error):
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        print(error.stderr.strip(), file=sys.stderr)
    print(error, file=sys.stderr)


def read_event():
    return json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    commands.add_parser("promote")
    commands.add_parser("check-pause")
    commands.add_parser("watch-tags")
    failure = commands.add_parser("retract")
    failure.add_argument("--tag", required=True)
    failure.add_argument("--reason", required=True)
    failure.add_argument("--evidence", required=True)
    failure.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command in ("plan", "promote"):
        candidate, message = plan(read_event())
        print(message, flush=True)
        if args.command == "plan":
            promotion.write_outputs(candidate or {})
        elif candidate:
            promotion.write_outputs({"tag": dispatch(candidate)})
    elif args.command == "check-pause":
        promotion.require(not paused(pause_variable() or ""), "SYNC_PAUSED is true; publication blocked")
    elif args.command == "watch-tags":
        tags = find_watch_tags(releases(), datetime.now(timezone.utc))
        promotion.write_outputs({"tags": json.dumps(tags)})
        print(f"Within the 24-hour watch window: {', '.join(tags) or 'none'}")
    else:
        retract(args.tag, args.reason, args.evidence, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        report_error(exc)
        sys.exit(1)
