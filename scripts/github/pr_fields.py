#!/usr/bin/env python3
"""Check the machine fields of a pull request body: Task, Agent, Reviewed head, Review.

An agent that merges on a review needs to know the review was of the code that will land.
`Reviewed head` must therefore equal the pull request's current head, so a push after the
review makes the check fail until the review is redone and the field updated.
Reads the pull_request event payload; the body is data and is never run or interpolated.
"""
import json
import os
import re
import sys

FIELDS = ("Task", "Agent", "Reviewed head", "Review")
PATTERNS = {
    "Task": re.compile(r"#[0-9]{1,7}", re.ASCII),
    "Agent": re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII),
    "Reviewed head": re.compile(r"[0-9a-f]{40}", re.ASCII),
    "Review": re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\S+", re.ASCII),
}
MESSAGES = {
    "Task": "Task: #<issue number>",
    "Agent": "Agent: <your-agent-name>",
    "Reviewed head": "Reviewed head: <the full 40-character sha the review covered>",
    "Review": "Review: <https://github.com/... link to the review>",
}


def values(body):
    """Each field's values, one per unindented `Name: value` line outside code fences."""
    found = {name: [] for name in FIELDS}
    fenced = False
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        for name in FIELDS:
            prefix = name + ": "
            if line.startswith(prefix):
                found[name].append(line[len(prefix):].rstrip(" "))
    return found


def problems(body, head_sha):
    """Human-readable problems; empty means the body passes."""
    found = values(body or "")
    out = []
    for name in FIELDS:
        got = found[name]
        if not got:
            out.append(f"missing field, add a line: {MESSAGES[name]}")
        elif len(got) > 1:
            out.append(f"`{name}:` appears {len(got)} times; keep exactly one")
        elif not PATTERNS[name].fullmatch(got[0]):
            out.append(f"`{name}:` has an invalid value; expected {MESSAGES[name]}")
    reviewed = found["Reviewed head"]
    if len(reviewed) == 1 and PATTERNS["Reviewed head"].fullmatch(reviewed[0]) and reviewed[0] != head_sha:
        out.append(f"`Reviewed head` is {reviewed[0][:12]} but the pull request head is {head_sha[:12]}: "
                   "the review is stale; re-review the current head and update the field")
    return out


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        pull = json.load(handle)["pull_request"]
    if pull["user"].get("type") == "Bot":
        print("Bot-authored pull request: fields not required.")
        return 0
    if pull.get("draft"):
        print("Draft pull request: fields are checked when it is marked ready for review.")
        return 0
    found = problems(pull.get("body") or "", pull["head"]["sha"])
    for item in found:
        print(f"::error title=PR fields::{item}")
    if not found:
        print("PR fields OK.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
