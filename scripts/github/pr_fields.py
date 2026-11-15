#!/usr/bin/env python3
"""Check the machine fields of a pull request body: Task, Agent, Reviewed head, Review.

An agent that merges on a review needs to know the review was of the code that will land.
`Reviewed head` must therefore equal the pull request's current head, so a push after the
review makes the check fail until the review is redone and the field updated.
Reads the pull_request event payload; the body is data and is never run or interpolated.

Advisory only. A pull request can edit the workflow that runs this checker, so a green
"PR fields" is never evidence that a review is current: read the review itself.
"""
import json
import os
import re
import sys

# Pull requests opened before this get a warning instead of a failure, so nothing already open
# turns red overnight. It compares the PR's created_at from the event payload, which the author
# cannot set. Set it to the merge time when this lands; the far-future default means "never
# enforced", the safe state if that edit is forgotten.
ENFORCED_FROM = "2099-01-01T00:00:00Z"

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
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def visible_lines(body):
    """Body lines outside fenced code blocks and HTML comments, fences tracked as CommonMark does.

    An opening fence is up to 3 spaces of indent plus 3 or more backticks or tildes; it closes
    only on the same character, at least as long, with nothing after it. An unclosed fence or
    comment hides the rest of the body, which can only cause a failure, never a pass."""
    kept, fence = [], None
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = FENCE.fullmatch(line)
        if fence is None:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = (match.group(1)[0], len(match.group(1)))
            else:
                kept.append(line)
        elif (match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1]
              and not match.group(2).strip()):
            fence = None
    return COMMENT.sub("", "\n".join(kept)).split("\n")


def values(body):
    """Each field's values, one per unindented `Name: value` line that is visible Markdown text."""
    found = {name: [] for name in FIELDS}
    for line in visible_lines(body):
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


def enforced(created_at, since=None):
    """True only for a pull request provably opened at or after the cutoff; unknown means warn."""
    return bool(created_at) and created_at >= (since or ENFORCED_FROM)


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
    strict = enforced(pull.get("created_at"))
    for item in found:
        print(f"::{'error' if strict else 'warning'} title=PR fields::{item}")
    if not found:
        print("PR fields OK.")
    elif not strict:
        print("Warning only: this pull request was opened before the rule took effect.")
    return 1 if found and strict else 0


if __name__ == "__main__":
    sys.exit(main())
