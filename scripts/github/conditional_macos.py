#!/usr/bin/env python3
"""Decide whether the advisory macOS PR check can use a local result."""

import datetime as dt
import json
import os
import re
import secrets
import subprocess
from urllib.parse import unquote

REPO = "alexandru-savinov/agterm-omarchy"
OWNER = "alexandru-savinov"
CONTEXT = "local/macos-validation"
FLAG_REF = "refs/heads/ci-state/conditional-macos"
FLAG_QUERY = f"git/matching-refs/{FLAG_REF.removeprefix('refs/')}"
FLAG_FILE = "macos-local-ci-paused.json"
SHA = re.compile(r"[0-9a-f]{40}")
DESCRIPTION = re.compile(r"v1 tree=([0-9a-f]{40}) exit=0 sha256=[0-9a-f]{64}")


def api(path):
    return json.loads(subprocess.check_output(
        ["gh", "api", "--hostname", "github.com", f"repos/{REPO}/{path}"], timeout=20
    ))


def matches(status, commit, tree, now):
    if status["state"] != "success" or (status.get("creator") or {}).get("login") != OWNER:
        return False
    age = now - dt.datetime.fromisoformat(status["created_at"])
    if not dt.timedelta(0) <= age < dt.timedelta(hours=24):
        return False
    description = DESCRIPTION.fullmatch(status.get("description") or "")
    if not description or description[1] != tree:
        return False
    prefix = f"https://github.com/{REPO}/commit/{commit}#local-check="
    url = status.get("target_url") or ""
    if not url.startswith(prefix):
        return False
    versions = json.loads(unquote(url[len(prefix):]))
    return isinstance(versions, dict) and all(
        isinstance(versions.get(key), str) and versions[key].strip()
        for key in ("macOS", "Xcode", "tools")
    )


def decide(event, commit, request=api, *, now=None, randbelow=secrets.randbelow):
    if event != "pull_request" or not SHA.fullmatch(commit):
        return "hosted"
    statuses = request(f"commits/{commit}/status?per_page=100")["statuses"]
    status = next((item for item in statuses if item["context"] == CONTEXT), None)
    if status is None:
        return "hosted"
    tree = request(f"git/commits/{commit}")["tree"]["sha"]
    if not matches(status, commit, tree, now or dt.datetime.now(dt.timezone.utc)):
        return "hosted"
    refs = request(FLAG_QUERY)
    if not isinstance(refs, list):
        return "hosted"
    flag = next((ref for ref in refs if ref["ref"] == FLAG_REF), None)
    if flag is not None:
        # The branch exists only to hold the pause record. A missing file also runs hosted.
        flag_commit = flag["object"]["sha"]
        if SHA.fullmatch(flag_commit):
            request(f"contents/{FLAG_FILE}?ref={flag_commit}")
        print("Conditional macOS skips are paused pending review.")
        return "hosted"
    return "sample" if randbelow(5) == 0 else "skip"


if __name__ == "__main__":
    try:
        decision = decide(os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"])
    except (subprocess.SubprocessError, OSError, AttributeError, KeyError, TypeError, ValueError):
        print("Local evidence unavailable. Running hosted macOS checks.")
        decision = "hosted"
    print(f"Conditional macOS decision: {decision}")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"decision={decision}\n")
