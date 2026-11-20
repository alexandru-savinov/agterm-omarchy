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
LABEL_INCIDENT = "release-incident"  # opened by auto-release on a retraction; closing it resumes syncs
SYNC_LABELS = frozenset({LABEL_CLEAN, LABEL_CONFLICT})
TAG_NAMESPACE = "refs/upstream-tags"
UPSTREAM = "umputun/agterm"
TASK = "#476"
LIST_BUDGET = 28000  # characters per list, so two lists stay below GitHub's 65536-character body limit
CI_WORKFLOW = ".github/workflows/omarchy-ci.yml"
RELEASE_WORKFLOW = ".github/workflows/release-linux.yml"
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


def pause_reason(paused_value, incidents):
    """Why syncing is paused, or "": SYNC_PAUSED, or any open release-incident issue."""
    if not isinstance(incidents, list):
        raise SyncError(f"open {LABEL_INCIDENT} issue list is not a JSON list")
    if paused(paused_value):
        return "SYNC_PAUSED is true"
    if incidents:
        numbers = ", ".join(f"#{issue.get('number')}" for issue in incidents)
        return f"an open {LABEL_INCIDENT} issue pauses syncing: {numbers}"
    return ""


def open_sync_prs(prs):
    """Open PRs that already carry a sync: by label, or by a same-repo `sync/umputun-*` head (a manual catch-up).

    Only maintainers can label, but anyone can name a fork branch, so a fork head never blocks."""
    if not isinstance(prs, list):
        raise SyncError("open PR list is not a JSON list")
    found = []
    for pr in prs:
        if not isinstance(pr.get("isCrossRepository"), bool):
            raise SyncError(f"open PR #{pr.get('number')} has no isCrossRepository field")
        labels = {label.get("name") for label in pr.get("labels") or []}
        same_repo_sync = not pr["isCrossRepository"] and str(pr.get("headRefName", "")).startswith(BRANCH_PREFIX)
        if labels & SYNC_LABELS or same_repo_sync:
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


def decide(*, paused_value, incidents, prs, tags, is_ancestor, remote_branches, override=""):
    """The run's decision. Gates in order: pause, one open sync PR, a release to sync, no existing branch.

    `blocked` (a branch with no open PR) fails the run: skipping it green would hide a stuck sync forever."""
    reason = pause_reason(paused_value, incidents)
    if reason:
        return {"action": "skip", "tag": "", "commit": "", "branch": "", "selection": "", "reason": reason}
    tag, commit, reason = select(tags, is_ancestor, override)
    plan = {"action": "skip", "tag": tag or "", "commit": commit, "branch": BRANCH_PREFIX + tag if tag else "",
            "selection": reason}
    blocking = open_sync_prs(prs)
    if blocking:
        numbers = ", ".join(f"#{pr.get('number')}" for pr in blocking)
        return {**plan, "reason": f"a sync PR is already open: {numbers}"}
    if not tag:
        return {**plan, "reason": reason}
    if plan["branch"] in remote_branches:
        return {**plan, "action": "blocked",
                "reason": f"branch {plan['branch']} exists with no open sync PR; delete it to retry this tag"}
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


def listing(lines):
    shown, used = [], 0
    for line in lines:
        used += len(line) + 1
        if used > LIST_BUDGET:
            return shown + [f"- and {len(lines) - len(shown)} more"]
        shown.append(line)
    return shown


def pr_text(*, tag, commit, base_sha, head_sha, run_url, conflicts, github_changes):
    """(title, label, draft, body) for the sync PR.

    Any change under .github/ forces the draft: upstream's workflows run on this branch once it is pushed."""
    release = f"https://github.com/{UPSTREAM}/releases/tag/{tag}"
    title = f"Sync umputun/agterm {tag}"
    # The fields describe the run that built the branch; the reviewer replaces both with their own review.
    fields = [f"Task: {TASK}", "Agent: upstream-sync", f"Reviewed head: {head_sha}", f"Review: {run_url}", ""]
    body = fields + [
        f"Merges [{UPSTREAM} {tag}]({release}) (`{commit}`) into main (`{base_sha}`).",
        "Opened by `.github/workflows/upstream-sync.yml`. Merge as a plain merge commit after CI on both "
        "arches, a fresh-context review with no HIGH or MEDIUM, and the native gates on the merge result.",
        "The merge updates `UPSTREAM_BASE_TAG` and `UPSTREAM_BASE_COMMIT` in `omarchy-ci.yml` and "
        "`release-linux.yml`. Resolve any conflicted pins before marking the PR ready.",
        "The fields above name the run that built this branch, not a review: the reviewer replaces them.", ""]
    if conflicts:
        body += [f"## Conflicts ({len(conflicts)})", "",
                 "The merge commit records these paths as committed with conflict markers (or the surviving side "
                 "of a delete conflict). Resolve them on this branch and adapt `agterm-linux` before "
                 "marking the PR ready.", "",
                 *listing([f"- `{path}` ({kind})" for path, kind in conflicts]), ""]
    if github_changes:
        body += [f"## Changes under .github/ ({len(github_changes)})", "",
                 "Review these before marking the PR ready; they already run on this branch.", "",
                 *listing([f"- `{path}`" for path in github_changes]), ""]
    if not conflicts and not github_changes:
        return title, LABEL_CLEAN, False, "\n".join(body + ["The merge applied cleanly.", ""])
    return title, LABEL_CONFLICT, True, "\n".join(body)


def git(*args, cwd=None, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise SyncError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result


def fetched_tags(namespace=TAG_NAMESPACE):
    """tag -> commit for every ref under the namespace the workflow fetched upstream tags into.

    `^{commit}` peels a tag of a tag fully, where for-each-ref's `*objectname` stops one level down."""
    refs = git("for-each-ref", "--format=%(refname)", namespace).stdout.split()
    if not refs:
        return {}
    commits = git("rev-parse", *(ref + "^{commit}" for ref in refs)).stdout.split()
    return {ref.removeprefix(namespace + "/"): commit for ref, commit in zip(refs, commits, strict=True)}


def ancestor_check(base):
    def is_ancestor(commit):
        result = git("merge-base", "--is-ancestor", commit, base, check=False)
        if result.returncode not in (0, 1):
            raise SyncError(f"ancestor check of {commit} failed: {result.stderr.strip()}")
        return result.returncode == 0
    return is_ancestor


IDENTITY = ("-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898283+github-actions[bot]@users.noreply.github.com")


def update_boundary(worktree, base, tag, commit, conflicted_paths):
    """Update the downstream boundary pins without changing any upstream-owned workflow."""
    for name in (CI_WORKFLOW, RELEASE_WORKFLOW):
        if name in conflicted_paths or git("cat-file", "-e", f"{base}:{name}", cwd=worktree, check=False).returncode:
            continue
        path = Path(worktree) / name
        if path.is_symlink():
            raise SyncError(f"cannot update boundary pins in symlink {name}")
        text = path.read_text(encoding="utf-8")
        for key, value in (("UPSTREAM_BASE_TAG", tag), ("UPSTREAM_BASE_COMMIT", commit)):
            text, count = re.subn(rf'^  {key}: "[^"\n]+"$', f'  {key}: "{value}"', text, flags=re.MULTILINE)
            if count != 1:
                raise SyncError(f"expected exactly one {key} in {name}")
        path.write_text(text, encoding="utf-8")
        git("add", "--", name, cwd=worktree)


def build(worktree, base, branch, tag, commit):
    """Create `branch` in a new worktree as base merged with commit; conflicts are committed with markers.

    Returns (conflicts, paths the merge changes under .github/)."""
    git("worktree", "add", "-b", branch, str(worktree), base)
    message = f"Merge {UPSTREAM} {tag} into main"
    merged = git(*IDENTITY, "merge", "--no-ff", "--no-commit", commit, cwd=worktree, check=False)
    conflicts = conflicts_from_status(git("status", "--porcelain=v1", "-z", cwd=worktree).stdout)
    if merged.returncode and not conflicts:
        raise SyncError(f"git merge failed without conflicts: {merged.stderr.strip() or merged.stdout.strip()}")
    if git("rev-parse", "--verify", "MERGE_HEAD", cwd=worktree, check=False).returncode:
        raise SyncError("sync commit must have both main and upstream parents")
    github = git("diff", "--name-only", "-z", base, "--", ".github", cwd=worktree).stdout
    update_boundary(worktree, base, tag, commit, {path for path, _ in conflicts})
    if conflicts:
        git("add", "-A", cwd=worktree)
        listing = "\n".join(path for path, _ in conflicts)
        git(*IDENTITY, "commit", "--no-verify", "-m", f"{message} (conflicts committed for adaptation)",
            "-m", f"Conflicted paths:\n{listing}", cwd=worktree)
    else:
        git(*IDENTITY, "commit", "--no-verify", "-m", message, cwd=worktree)
    parents = git("rev-list", "--parents", "-n", "1", "HEAD", cwd=worktree).stdout.split()[1:]
    if parents != [git("rev-parse", base).stdout.strip(), commit]:
        raise SyncError(f"sync commit parents are {parents}, expected base then {commit}")
    return conflicts, sorted(set(github.split("\0")) - {""})


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
    plan_cmd.add_argument("--open-prs", required=True, type=Path,
                          help="gh pr list --json number,headRefName,isCrossRepository,labels")
    plan_cmd.add_argument("--remote-branches", required=True, type=Path, help="git ls-remote --heads output")
    plan_cmd.add_argument("--paused", default="")
    plan_cmd.add_argument("--incidents", required=True, type=Path,
                          help=f"gh issue list --state open --label {LABEL_INCIDENT} --json number")
    plan_cmd.add_argument("--tag", default="")
    merge_cmd = sub.add_parser("merge")
    for name in ("--base", "--branch", "--tag", "--commit", "--worktree", "--body", "--run-url"):
        merge_cmd.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            incidents = json.loads(args.incidents.read_text(encoding="utf-8"))
            prs = json.loads(args.open_prs.read_text(encoding="utf-8"))
            branches = {line.split("\t")[1].removeprefix("refs/heads/")
                        for line in args.remote_branches.read_text(encoding="utf-8").splitlines() if "\t" in line}
            base = git("rev-parse", "--verify", args.base + "^{commit}").stdout.strip()
            emit(decide(paused_value=args.paused, incidents=incidents, prs=prs, tags=fetched_tags(), is_ancestor=ancestor_check(base),
                        remote_branches=branches, override=args.tag.strip()))
        else:
            base = git("rev-parse", "--verify", args.base + "^{commit}").stdout.strip()
            conflicts, github_changes = build(Path(args.worktree), base, args.branch, args.tag, args.commit)
            head = git("rev-parse", "HEAD", cwd=args.worktree).stdout.strip()
            title, label, draft, body = pr_text(tag=args.tag, commit=args.commit, base_sha=base, head_sha=head,
                                                run_url=args.run_url, conflicts=conflicts,
                                                github_changes=github_changes)
            Path(args.body).write_text(body, encoding="utf-8")
            emit({"title": title, "label": label, "draft": str(draft).lower(), "conflicts": len(conflicts),
                  "github_changes": len(github_changes), "base": base, "head": head})
    except (SyncError, OSError, ValueError) as error:
        print(f"upstream-sync: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
