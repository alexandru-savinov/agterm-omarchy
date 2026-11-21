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
WATCH_UNAVAILABLE = "release-watch-unavailable"
RELEASES_PATH = "releases?per_page=100"
PROMOTION_RUNS = "actions/workflows/prerelease-omarchy.yml/runs"
ACTIVE_PROMOTION_STATUSES = ("queued", "in_progress", "requested", "waiting", "pending")
# Every job omarchy-ci.yml defines, with {arch} for a matrix over ARCHITECTURES; a test pins this to the file.
CI_JOBS = ("Installer and scripts", "Detect changes", "Core tests ({arch})", "SwiftLint (Linux)",
           "GTK build and tests ({arch})", "Attest CI archive ({arch})", "libghostty patch Zig tests ({arch})",
           "zmx patch tests", "Upstream workflow guard", "CI", "Cookbook checks")
# Omarchy CI path-filters every job except these when a change touches no product input. "Detect changes"
# applies the filter, "CI" aggregates every result, and the cookbook check builds nothing.
UNFILTERED_JOBS = ("Upstream workflow guard", "Installer and scripts", "Detect changes", "CI", "Cookbook checks")
SCOPE_GATES = ("Detect changes", "CI")
BUILD_JOBS = ("SwiftLint (Linux)",) + promotion.REQUIRED_ARCH_JOBS + (promotion.ATTEST_JOB,)
MATRIX = "${{ matrix.arch }}"
# GitHub allows a workflow run at most 50 attempts.
MAX_ATTEMPTS = 50


def gh(path, *, method="GET", data=None):
    command = ["gh", "api", "-X", method, "-H", "X-GitHub-Api-Version: 2026-03-10",
               f"repos/{promotion.REPOSITORY}/{path}"]
    kwargs = {}
    if data is not None:
        command += ["--input", "-"]
        kwargs["input"] = json.dumps(data)
    result = subprocess.run(command, text=True, check=True, capture_output=True, **kwargs)
    return json.loads(result.stdout) if result.stdout.strip() else None


def open_issues(label):
    pages = promotion.api(f"issues?labels={label}&state=open&per_page=100", pages=True)
    return (item for page in pages for item in page if "pull_request" not in item)


def pause_reason():
    if os.environ.get("SYNC_PAUSED", "").strip().casefold() not in ("", "false"):
        return "SYNC_PAUSED is set"
    if next(open_issues(INCIDENT), None) is not None:
        return "An open release-incident issue is holding releases"
    return ""


def releases():
    return [item for page in promotion.api(RELEASES_PATH, pages=True) for item in page
            if TAG.fullmatch(item["tag_name"])]


def next_version(base, tags):
    serials = [int(match["serial"]) for tag in tags
               if (match := TAG.fullmatch(tag)) and match["base"] == base]
    return f"{base}.{max(serials, default=0) + 1}"


def filtered_forms(template):
    """The names a job can take: one unexpanded matrix job when skipped, or every architecture."""
    if "{arch}" not in template:
        return [{template}]
    return [{template.format(arch=MATRIX)}, {template.format(arch=arch) for arch in promotion.ARCHITECTURES}]


def inventory_gap(jobs):
    """Why jobs is not exactly one complete Omarchy CI job list, or '' when it is."""
    names = [job["name"] for job in jobs]
    if len(names) != len(set(names)):
        return "a job is listed twice"
    known = set()
    for template in CI_JOBS:
        forms = filtered_forms(template)
        present = {name for name in names if any(name in form for form in forms)}
        if present not in forms:
            return f"{template.format(arch='*')} is incomplete"
        known |= present
    if unknown := sorted(set(names) - known):
        return f"unrecognized {', '.join(unknown)}"
    return ""


def jobs_that_ran(jobs):
    return sorted(job["name"] for job in jobs if job["name"] not in UNFILTERED_JOBS and job["conclusion"] != "skipped")


def ci_scope(run, jobs, earlier=()):
    """Skip only on proof that a green CI run built nothing; anything else proceeds to the full checks.

    jobs is the triggering attempt; earlier holds the job lists of the run's previous attempts, None for
    one that could not be read."""
    promotion.require(run.get("conclusion") == "success", "Omarchy CI did not succeed; nothing can be released")
    label = f"Omarchy CI run {run['id']} attempt {run['run_attempt']}"
    if gap := inventory_gap(jobs):
        return "proceed", f"{label}: {gap}; the release checks decide"
    for name in SCOPE_GATES:
        if next(job for job in jobs if job["name"] == name)["conclusion"] != "success":
            return "proceed", f"{label}: {name} did not succeed; the release checks decide"
    if ran := jobs_that_ran(jobs):
        return "proceed", f"{label}: {', '.join(ran)} ran; the release checks decide"
    # Each attempt lists its own results, so a later empty attempt must not hide an earlier one that built.
    # An earlier attempt that cannot be read in full is no proof either.
    if len(earlier) != run["run_attempt"] - 1:
        return "proceed", f"{label}: earlier attempts are missing; the release checks decide"
    for number, attempt in enumerate(earlier, start=1):
        if attempt is None:
            return "proceed", f"{label}: attempt {number} could not be read; the release checks decide"
        if gap := inventory_gap(attempt):
            return "proceed", f"{label}: attempt {number}: {gap}; the release checks decide"
        if ran := jobs_that_ran(attempt):
            return "proceed", f"{label}: attempt {number} ran {', '.join(ran)}; the release checks decide"
    names = ", ".join(template.format(arch="*") for template in BUILD_JOBS)
    return "skip", f"{label} built nothing to release: path filters skipped {names}"


def earlier_attempt(run, number):
    try:
        return promotion.run_jobs({"id": run["id"], "run_attempt": number})
    except (subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        report_error(error)
        return None


def write_summary(text):
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"### Automatic release scope\n{text}\n")


def plan(event):
    source = event["workflow_run"]
    if (source["event"], source["head_branch"], source["conclusion"]) != ("push", "main", "success"):
        return None, "Expected a successful main push"
    if source["head_repository"]["full_name"] != promotion.REPOSITORY:
        return None, "Expected a same-repository push"
    if reason := pause_reason():
        return None, reason
    run_id, sha, attempt = source["id"], source["head_sha"], source["run_attempt"]
    head = promotion.api("git/ref/heads/main")["object"]["sha"]
    if head != sha:
        return None, "A newer main commit superseded this CI run"
    run = promotion.api(f"actions/runs/{run_id}")
    if run["run_attempt"] != attempt or run["status"] != "completed":
        return None, "The triggering attempt is no longer current and completed"
    promotion.require(run["id"] == run_id and run["event"] == "push" and run["head_branch"] == "main",
                      "Not the requested main push run")
    promotion.validate_run(run, promotion.api(promotion.WORKFLOW_API), sha, head)
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
    if any(promotion.api(f"{PROMOTION_RUNS}?branch=main&event=workflow_dispatch&status={status}&per_page=1")["total_count"]
           for status in ACTIVE_PROMOTION_STATUSES):
        return None, "A promotion workflow is already active"
    # A retraction, draft, or partially published release can leave a tag behind.
    refs = promotion.api("git/matching-refs/tags/omarchy-v?per_page=100", pages=True)
    tags = [item["ref"].removeprefix("refs/tags/") for page in refs for item in page]
    version = next_version(base, tags + [item["tag_name"] for item in items])
    return {"run_id": str(run_id), "sha": sha, "attempt": str(attempt), "version": version}, "Candidate selected"


def promoted_automatically(release):
    assets = [asset for asset in release.get("assets", []) if asset["name"] == "promotion.json"
              and asset["state"] == "uploaded" and asset["size"] <= 65536]
    if len(assets) != 1:
        return False
    path = f"repos/{promotion.REPOSITORY}/releases/assets/{int(assets[0]['id'])}"
    data = subprocess.check_output(["gh", "api", "-H", "Accept: application/octet-stream", path])
    try:
        record = json.loads(data)
    except (ValueError, UnicodeError):
        return False
    if not isinstance(record, dict) or record.get("tag") != release["tag_name"]:
        return False
    run_id = record.get("auto_release_run_id")
    return type(run_id) is int and run_id > 0


def find_watch_tags(items, now):
    tags = []
    for item in items:
        if item["draft"] or item["prerelease"] or not item["published_at"]:
            continue
        published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        if timedelta(0) <= now - published < WATCH_WINDOW and promoted_automatically(item):
            tags.append(item["tag_name"])
    return tags


def dispatch(candidate, gate):
    if reason := pause_reason():
        print(f"{reason}; promotion skipped.")
        return ""
    promotion.require(isinstance(gate, dict) and gate.get("passed") == "true" and gate.get("attested") == "true",
                      "Automatic promotion requires a green, attested native verdict")
    # Actions can omit empty outputs. A CI-artifact gate has no release tag.
    promotion.require(gate.get("source_commit") == candidate["sha"]
                      and gate.get("source_run_id") == candidate["run_id"]
                      and gate.get("source_run_attempt") == candidate["attempt"]
                      and gate.get("release_tag", "") == "",
                      "Native evidence does not identify the selected CI run, attempt and commit")
    archives = promotion.parse_native_archives(gate.get("archives", ""), candidate["sha"])
    auto_run_id = os.environ.get("GITHUB_RUN_ID", "")
    promotion.require(re.fullmatch(r"[1-9][0-9]*", auto_run_id), "Automatic promotion requires GITHUB_RUN_ID")
    response = gh("actions/workflows/prerelease-omarchy.yml/dispatches", method="POST", data={
        "ref": "main",
        "inputs": {"source_run": candidate["run_id"], "source_sha": candidate["sha"],
                   "source_attempt": gate["source_run_attempt"], "native_archives": json.dumps(archives),
                   "auto_release_run_id": auto_run_id,
                   "version": candidate["version"], "dry_run": False},
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


def record_issue(label, title, description, body):
    if any(item["title"] == title for item in open_issues(label)):
        return
    if promotion.absent(f"labels/{label}"):
        try:
            gh("labels", method="POST", data={"name": label, "color": "B60205", "description": description})
        except subprocess.CalledProcessError:
            if promotion.absent(f"labels/{label}"):
                raise
    issue = gh("issues", method="POST", data={"title": title, "body": body, "labels": [label]})
    print(f"Issue: {issue['html_url']}")


def record_incident(tag, reason, evidence):
    body = (f"The post-release watch failed for [`{tag}`](https://github.com/"
            f"{promotion.REPOSITORY}/releases/tag/{tag}).\n\n"
            f"- Failure: {reason}\n"
            f"- Evidence: {evidence}\n\n"
            "This open issue pauses automation. Check the linked run for the prerelease edit. "
            "Close the issue to clear this hold after investigating and preparing a fix. "
            "Keep the release and tag for incident evidence.")
    record_issue(INCIDENT, f"Release incident: {tag}", "Published Omarchy release failed its watch", body)


def retract(tag, reason, evidence):
    promotion.require(TAG.fullmatch(tag), "Expected a canonical versioned Omarchy tag")
    # Open the hold first. A failed issue write must still attempt to retract.
    try:
        record_incident(tag, reason, evidence)
    finally:
        mark_prerelease(tag)


def watch_verdict(tag, attempts):
    for attempt in attempts.values():
        result, outputs = attempt["result"], attempt.get("outputs") or {}
        if outputs.get("release_tag") != tag or outputs.get("attested") != "true":
            continue
        if result == "success" and outputs.get("passed") == "true":
            return "passed"
        if result == "failure" and outputs.get("passed") == "false" and outputs.get("gate_failed") == "true":
            return "failed"
    return "missing"


def watch_action(verdict, urls):
    if verdict == "failed" or (verdict == "passed" and urls == "failure"):
        return "retract"
    if verdict == "passed" and urls == "success":
        return "keep"
    return "report"


def record_watch_unavailable(tag, evidence):
    promotion.require(TAG.fullmatch(tag), "Expected a canonical versioned Omarchy tag")
    record_issue(WATCH_UNAVAILABLE, f"Release watch unavailable: {tag}", "Release watch could not establish a verdict",
                 f"The post-release watch could not establish a verdict for `{tag}`. "
                 f"The release remains published and automation can continue.\n\n[Watch run]({evidence})")


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
    commands.add_parser("ci-scope")
    commands.add_parser("plan")
    commands.add_parser("promote")
    commands.add_parser("check-pause")
    commands.add_parser("watch-tags")
    commands.add_parser("watch-verdict")
    commands.add_parser("watch-action")
    unavailable = commands.add_parser("watch-unavailable")
    unavailable.add_argument("--tag", required=True)
    unavailable.add_argument("--evidence", required=True)
    failure = commands.add_parser("retract")
    failure.add_argument("--tag", required=True)
    failure.add_argument("--reason", required=True)
    failure.add_argument("--evidence", required=True)
    args = parser.parse_args()
    if args.command == "ci-scope":
        run = read_event()["workflow_run"]
        attempt = run["run_attempt"]
        if type(attempt) is not int or not 0 < attempt <= MAX_ATTEMPTS:
            raise ValueError(f"Unexpected Omarchy CI run attempt {attempt!r}")
        earlier = [earlier_attempt(run, number) for number in range(1, attempt)]
        scope, reason = ci_scope(run, promotion.run_jobs(run), earlier)
        promotion.write_outputs({"scope": scope})
        write_summary(f"{'Skipped' if scope == 'skip' else 'Proceeding'}: {reason}.")
        print(f"{scope}: {reason}")
    elif args.command in ("plan", "promote"):
        candidate, message = plan(read_event())
        print(message, flush=True)
        if args.command == "plan":
            promotion.write_outputs(candidate or {})
        elif candidate:
            promotion.write_outputs({"tag": dispatch(candidate, json.loads(os.environ["NATIVE_GATE"]))})
    elif args.command == "check-pause":
        if reason := pause_reason():
            raise ValueError(f"{reason}; publication blocked")
    elif args.command == "watch-tags":
        tags = find_watch_tags(releases(), datetime.now(timezone.utc))
        promotion.write_outputs({"tags": json.dumps(tags)})
        print(f"Within the 24-hour watch window: {', '.join(tags) or 'none'}")
    elif args.command == "watch-verdict":
        attempts = json.loads(os.environ["NATIVE_ATTEMPTS"])
        promotion.write_outputs({"verdict": watch_verdict(os.environ["TAG"], attempts)})
    elif args.command == "watch-action":
        action = watch_action(os.environ["NATIVE_VERDICT"], os.environ.get("URL_RESULT", ""))
        promotion.write_outputs({"action": action})
        print(f"Release watch action: {action}")
    elif args.command == "watch-unavailable":
        record_watch_unavailable(args.tag, args.evidence)
    else:
        retract(args.tag, args.reason, args.evidence)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        report_error(exc)
        sys.exit(1)
