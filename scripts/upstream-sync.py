#!/usr/bin/env python3
"""Plan and build one umputun/agterm release sync branch for upstream-sync.yml (#476).

`plan` decides whether this run opens a sync PR and for which tag; `merge` builds the branch in a
separate worktree. Neither pushes or talks to GitHub: the workflow feeds in the open PRs and the
existing remote branches, and owns every write.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)
BRANCH_PREFIX = "sync/umputun-"
LABEL_CLEAN, LABEL_CONFLICT = "upstream-sync", "needs-adaptation"
SYNC_LABELS = frozenset({LABEL_CLEAN, LABEL_CONFLICT})
TAG_NAMESPACE = "refs/upstream-tags"
UPSTREAM = "umputun/agterm"
MAX_LISTED_CONFLICTS = 400  # keeps the body far below GitHub's 65536-character limit
UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
CONFLICT_KINDS = {"DD": "both deleted", "AU": "added by us", "UD": "deleted by them", "UA": "added by them",
                  "DU": "deleted by us", "AA": "both added", "UU": "both modified"}


class SyncError(Exception):
    pass


def version(tag):
    match = TAG.fullmatch(tag)
    return tuple(int(part) for part in match.groups()) if match else None


def paused(value):
    return (value or "").strip().lower() == "true"


def open_sync_prs(prs):
    """Open PRs that already carry a sync: by label, or by a `sync/umputun-*` head such as a manual catch-up."""
    if not isinstance(prs, list):
        raise SyncError("open PR list is not a JSON list")
    found = []
    for pr in prs:
        labels = {label.get("name") for label in pr.get("labels") or []}
        if labels & SYNC_LABELS or str(pr.get("headRefName", "")).startswith(BRANCH_PREFIX):
            found.append(pr)
    return found


def select(tags, is_ancestor, override=""):
    """(tag, commit, reason) for the release to sync; tag is None when there is nothing to do.

    Only the newest release is a candidate: an older tag that is not an ancestor (a maintenance
    branch release cut after a newer one merged) is never synced backwards."""
    releases = {tag: commit for tag, commit in tags.items() if version(tag)}
    if override:
        if not version(override):
            raise SyncError(f"tag override {override!r} is not a vX.Y.Z release tag")
        if override not in releases:
            raise SyncError(f"tag override {override} is not a tag of {UPSTREAM}")
        candidate = override
    elif not releases:
        raise SyncError(f"no vX.Y.Z tags fetched from {UPSTREAM}")
    else:
        candidate = max(releases, key=version)
    commit = releases[candidate]
    if is_ancestor(commit):
        return None, commit, f"main already contains {candidate}"
    return candidate, commit, f"{candidate} is not an ancestor of main"


def decide(*, paused_value, prs, tags, is_ancestor, remote_branches, override=""):
    """The run's decision. Gates in order: pause, one open sync PR, a release to sync, no existing branch."""
    tag, commit, reason = select(tags, is_ancestor, override)
    plan = {"action": "skip", "tag": tag or "", "commit": commit, "branch": BRANCH_PREFIX + tag if tag else "",
            "selection": reason}
    if paused(paused_value):
        return {**plan, "reason": "SYNC_PAUSED is true"}
    blocking = open_sync_prs(prs)
    if blocking:
        numbers = ", ".join(f"#{pr.get('number')}" for pr in blocking)
        return {**plan, "reason": f"a sync PR is already open: {numbers}"}
    if not tag:
        return {**plan, "reason": reason}
    if plan["branch"] in remote_branches:
        return {**plan, "reason": f"branch {plan['branch']} already exists; delete it to retry this tag"}
    return {**plan, "action": "sync", "reason": reason}


def conflicts_from_status(porcelain):
    """(path, kind) for every unmerged entry of `git status --porcelain=v1 -z`."""
    out = []
    entries = porcelain.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            index += 1  # the rename source follows as its own field
        if code in UNMERGED:
            out.append((path, CONFLICT_KINDS[code]))
    return sorted(out)


def pr_text(tag, commit, base_sha, conflicts):
    """(title, label, draft, body) for the sync PR."""
    release = f"https://github.com/{UPSTREAM}/releases/tag/{tag}"
    title = f"Sync umputun/agterm {tag}"
    head = ["Task: #", "Agent: upstream-sync", "Reviewed head:", "Review:", ""]
    intro = [f"Merges [{UPSTREAM} {tag}]({release}) (`{commit}`) into main (`{base_sha}`).",
             "Opened by `.github/workflows/upstream-sync.yml`. Merge as a plain merge commit after CI on both "
             "arches, a fresh-context review with no HIGH or MEDIUM, and the native gates on the merge result.", ""]
    if not conflicts:
        return title, LABEL_CLEAN, False, "\n".join(head + intro + ["The merge applied cleanly.", ""])
    listed = [f"- `{path}` ({kind})" for path, kind in conflicts[:MAX_LISTED_CONFLICTS]]
    if len(conflicts) > MAX_LISTED_CONFLICTS:
        listed.append(f"- and {len(conflicts) - MAX_LISTED_CONFLICTS} more")
    body = head + intro + [
        f"## Conflicts ({len(conflicts)})", "",
        "The merge commit records these paths as committed with conflict markers (or the surviving side of a "
        "delete conflict). Resolve them on this branch, adapt `agterm-linux`, and move `UPSTREAM_BASE_TAG` / "
        "`UPSTREAM_BASE_COMMIT` in `ci.yml`, then mark the PR ready.", "", *listed, ""]
    return title, LABEL_CONFLICT, True, "\n".join(body)


def git(*args, cwd=None, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise SyncError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result


def fetched_tags(namespace=TAG_NAMESPACE):
    """tag -> peeled commit for every ref under the namespace the workflow fetched upstream tags into."""
    lines = git("for-each-ref", "--format=%(refname)%00%(*objectname)%00%(objectname)", namespace).stdout
    tags = {}
    for line in lines.splitlines():
        ref, peeled, direct = line.split("\0")
        tags[ref.removeprefix(namespace + "/")] = peeled or direct
    return tags


def ancestor_check(base):
    def is_ancestor(commit):
        result = git("merge-base", "--is-ancestor", commit, base, check=False)
        if result.returncode not in (0, 1):
            raise SyncError(f"ancestor check of {commit} failed: {result.stderr.strip()}")
        return result.returncode == 0
    return is_ancestor


IDENTITY = ("-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898283+github-actions[bot]@users.noreply.github.com")


def build(worktree, base, branch, tag, commit):
    """Create `branch` in a new worktree as base merged with commit; conflicts are committed with markers."""
    git("worktree", "add", "-b", branch, str(worktree), base)
    message = f"Merge {UPSTREAM} {tag} into main"
    merged = git(*IDENTITY, "merge", "--no-ff", "--no-edit", "-m", message, commit, cwd=worktree, check=False)
    conflicts = conflicts_from_status(git("status", "--porcelain=v1", "-z", cwd=worktree).stdout)
    if merged.returncode and not conflicts:
        raise SyncError(f"git merge failed without conflicts: {merged.stderr.strip() or merged.stdout.strip()}")
    if conflicts:
        git("add", "-A", cwd=worktree)
        listing = "\n".join(path for path, _ in conflicts)
        git(*IDENTITY, "commit", "--no-verify", "-m", f"{message} (conflicts committed for adaptation)",
            "-m", f"Conflicted paths:\n{listing}", cwd=worktree)
    parents = git("rev-list", "--parents", "-n", "1", "HEAD", cwd=worktree).stdout.split()[1:]
    if parents != [git("rev-parse", base).stdout.strip(), commit]:
        raise SyncError(f"sync commit parents are {parents}, expected base then {commit}")
    return conflicts


def emit(values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as output:
            for key, value in values.items():
                if "\n" in str(value):
                    raise SyncError(f"output {key} spans lines")
                output.write(f"{key}={value}\n")
    print(json.dumps(values, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--base", required=True)
    plan_cmd.add_argument("--open-prs", required=True, type=Path, help="gh pr list --json number,headRefName,labels")
    plan_cmd.add_argument("--remote-branches", required=True, type=Path, help="git ls-remote --heads output")
    plan_cmd.add_argument("--paused", default="")
    plan_cmd.add_argument("--tag", default="")
    merge_cmd = sub.add_parser("merge")
    for name in ("--base", "--branch", "--tag", "--commit", "--worktree", "--body"):
        merge_cmd.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            prs = json.loads(args.open_prs.read_text(encoding="utf-8"))
            branches = {line.split("\t")[1].removeprefix("refs/heads/")
                        for line in args.remote_branches.read_text(encoding="utf-8").splitlines() if "\t" in line}
            base = git("rev-parse", "--verify", args.base + "^{commit}").stdout.strip()
            emit(decide(paused_value=args.paused, prs=prs, tags=fetched_tags(), is_ancestor=ancestor_check(base),
                        remote_branches=branches, override=args.tag.strip()))
        else:
            base = git("rev-parse", "--verify", args.base + "^{commit}").stdout.strip()
            conflicts = build(Path(args.worktree), base, args.branch, args.tag, args.commit)
            title, label, draft, body = pr_text(args.tag, args.commit, base, conflicts)
            Path(args.body).write_text(body, encoding="utf-8")
            emit({"title": title, "label": label, "draft": str(draft).lower(), "conflicts": len(conflicts),
                  "head": git("rev-parse", "HEAD", cwd=args.worktree).stdout.strip()})
    except (SyncError, OSError, ValueError) as error:
        print(f"upstream-sync: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
