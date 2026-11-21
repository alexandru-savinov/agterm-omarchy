#!/usr/bin/env python3
"""Check the machine fields of a pull request body: Task, Agent, Reviewed head, Review.

An agent that merges on a review needs to know the review was of the code that will land.
`Reviewed head` must therefore equal the pull request's current head, so a push after the
review makes the check fail until the review is redone and the field updated.
Reads the pull_request event payload; the body is data and is never run or interpolated.

The four fields are one `Name: value` per line, in any order, in one of two places:
the leading block (the first non-blank lines of the body, as upstream-sync.py writes them), or
the block that directly follows a line that is exactly `<details><summary>Review record</summary>`
and is closed by `</details>` (the pull request template puts it at the bottom);
a fold inside a fenced code block or an HTML comment does not count.
Everything else is ignored, so the checker never has to guess what Markdown would hide or
render (fences, comments, HTML). Fields in both places fail.

Advisory only. A pull request can edit the workflow that runs this checker, so a green
"PR fields" is never evidence that a review is current: read the review itself.
"""
import json
import os
import re
import sys

# Pull requests opened before this get a warning instead of a failure, so nothing already open
# turns red overnight. It compares the PR's created_at from the event payload, which the author
# cannot set. Set it to the merge time in a second reviewed commit (issue #423); the far-future
# default means "never enforced", the safe state while that has not happened.
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
LINE = re.compile(r"(" + "|".join(re.escape(name) for name in FIELDS) + r"): (.*)", re.DOTALL)
FOLD_OPEN = "<details><summary>Review record</summary>"
FOLD_CLOSE = "</details>"
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def fenced(lines):
    """Indexes of lines inside or delimiting a fenced code block, so a fold shown as an example never counts."""
    inside, fence = set(), None
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if fence:
            inside.add(index)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                    and not line[match.end():].strip():
                fence = None
        elif match and not (match.group(1)[0] == "`" and "`" in line[match.end():]):
            inside.add(index)
            fence = match.group(1)
    return inside


def block(lines, start):
    """The `Name: value` matches from the first non-blank line at or after `start`, and the index after them."""
    while start < len(lines) and not lines[start].strip():
        start += 1
    matches = []
    while start < len(lines) and (match := LINE.fullmatch(lines[start])):
        matches.append(match)
        start += 1
    return matches, start


def split(body):
    return body.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def blocks(body):
    """(leading-block matches, Review record fold matches, number of fold openers)."""
    leading, _ = block(split(body), 0)
    # HTML comments go first, wherever they open; an unterminated one runs to the end of the body.
    lines = split(COMMENT.sub("", body))
    code = fenced(lines)
    folds = [index for index, line in enumerate(lines) if line == FOLD_OPEN and index not in code]
    inner = []
    if len(folds) == 1:
        inner, end = block(lines, folds[0] + 1)
        while end < len(lines) and not lines[end].strip():
            end += 1
        if end >= len(lines) or lines[end] != FOLD_CLOSE:
            inner = []
    return leading, inner, len(folds)


def values(body):
    """Each field's values from the leading block and from a single closed Review record fold."""
    found = {name: [] for name in FIELDS}
    leading, inner, _ = blocks(body)
    for match in leading + inner:
        found[match.group(1)].append(match.group(2).rstrip(" "))
    return found


def problems(body, head_sha):
    """Human-readable problems; empty means the body passes."""
    found = values(body or "")
    leading, inner, folds = blocks(body or "")
    out = [f"`{FOLD_OPEN}` appears {folds} times; keep exactly one"] if folds > 1 else []
    if leading and inner:
        out.append("fields appear both in the first lines and in the Review record fold; keep them in one place")
    for name in FIELDS:
        got = found[name]
        if not got:
            out.append(f"missing field, put it in the Review record fold or the first lines of the body: {MESSAGES[name]}")
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
