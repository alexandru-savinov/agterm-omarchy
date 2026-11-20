#!/usr/bin/env python3
"""Plan and build one upstream sync branch for upstream-sync.yml (#476, #493).

The primary source is melonamin/agterm-linux `linux-port`, which carries each umputun/agterm release
plus the Linux adaptations. A direct umputun release sync is the fallback, taken only for a release
linux-port has lacked for more than LINUX_PORT_LAG_DAYS.

`plan` decides whether this run opens a sync PR and from which source; `merge` builds the branch in a
separate worktree; `guard` checks the repository settings a sync push relies on. None pushes or talks
to GitHub: the workflow feeds in every listing, and owns every write.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)
SOURCE_LINUX_PORT, SOURCE_UMPUTUN = "linux-port", "umputun"
BRANCH_PREFIXES = {SOURCE_LINUX_PORT: "sync/linux-port-", SOURCE_UMPUTUN: "sync/umputun-"}
LINUX_PORT_LAG_DAYS = 7  # how long linux-port may lack an umputun release before that release syncs directly
DAY = 86400
LABEL_CLEAN, LABEL_CONFLICT = "upstream-sync", "needs-adaptation"
LABEL_INCIDENT = "release-incident"  # opened by auto-release on a retraction; closing it resumes syncs
SYNC_LABELS = frozenset({LABEL_CLEAN, LABEL_CONFLICT})
TAG_NAMESPACE = "refs/upstream-tags"
UPSTREAM = "umputun/agterm"
LINUX_PORT, LINUX_PORT_BRANCH = "melonamin/agterm-linux", "linux-port"
LINUX_PORT_REF = "refs/upstream-heads/linux-port"  # where the workflow fetches the linux-port head
TASK = "#476"
UNDECIDED = 3  # `pause` exit status: SYNC_PAUSED does not pause, so the incident listing decides
BASE_PIN_FILES = (".github/workflows/ci.yml", ".github/workflows/release-linux.yml")
LIST_BUDGET = 28000  # characters per list, so two lists stay below GitHub's 65536-character body limit
UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
CONFLICT_KINDS = {"DD": "both deleted", "AU": "added by us", "UD": "deleted by them", "UA": "added by them",
                  "DU": "deleted by us", "AA": "both added", "UU": "both modified"}


class SyncError(Exception):
    pass


def version(tag):
    match = TAG.fullmatch(tag)
    return tuple(int(part) for part in match.groups()) if match else None


def is_true(value):
    return (value or "").strip().lower() == "true"


def pause_reason(paused_value, incidents):
    """Why syncing is paused, or "": SYNC_PAUSED, or any open release-incident issue."""
    if not isinstance(incidents, list):
        raise SyncError(f"open {LABEL_INCIDENT} issue list is not a JSON list")
    if is_true(paused_value):
        return "SYNC_PAUSED is true"
    if incidents:
        numbers = ", ".join(f"#{issue.get('number')}" for issue in incidents)
        return f"an open {LABEL_INCIDENT} issue pauses syncing: {numbers}"
    return ""


def open_sync_prs(prs):
    """Same-repo open PRs that already carry a sync: by label, or by a sync-branch head (a manual catch-up).

    A PR from a fork never blocks, whatever its label or branch name."""
    if not isinstance(prs, list):
        raise SyncError("open PR list is not a JSON list")
    found = []
    for pr in prs:
        if not isinstance(pr.get("isCrossRepository"), bool):
            raise SyncError(f"open PR #{pr.get('number')} has no isCrossRepository field")
        if pr["isCrossRepository"]:
            continue
        labels = {label.get("name") for label in pr.get("labels") or []}
        if labels & SYNC_LABELS or str(pr.get("headRefName", "")).startswith(tuple(BRANCH_PREFIXES.values())):
            found.append(pr)
    return found


def newest_contained(tags, is_ancestor):
    """The newest release tag whose commit `is_ancestor` accepts, or None."""
    for tag in sorted((tag for tag in tags if version(tag)), key=version, reverse=True):
        if is_ancestor(tags[tag]):
            return tag
    return None


def lag_clocks(tags, releases, first_seen, now):
    """(tag -> the epoch its lag runs from, the first-seen record to keep) for every release tag.

    The clock is the GitHub release's `published_at`, or, for a tag with no published release, the first
    run that saw it. Never a git date: a lightweight tag pushed today carries its commit's date, and the
    release's `created_at` repeats it. A lost record restarts at `now`, so a clock is only ever late."""
    if not isinstance(releases, list) or not all(isinstance(release, dict) for release in releases):
        raise SyncError(f"{UPSTREAM} release list is not a JSON list of objects")
    if not isinstance(first_seen, dict) or not all(type(seen) is int for seen in first_seen.values()):
        raise SyncError("first-seen record is not a JSON object of epoch seconds")
    published = {}
    for release in releases:
        stamp = release.get("published_at")
        if release.get("draft") is False and isinstance(stamp, str):
            try:
                moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError as error:
                raise SyncError(f"release {release.get('tag_name')} has an unreadable published_at") from error
            if moment.tzinfo is None:
                raise SyncError(f"release {release.get('tag_name')} has a published_at without a time zone")
            published[release.get("tag_name")] = int(moment.timestamp())
    clocks, kept = {}, {}
    for tag in tags:
        if not version(tag):
            continue
        if tag in published:
            clocks[tag] = published[tag]
        else:
            clocks[tag] = kept[tag] = first_seen.get(tag, now)
    return clocks, kept


def select(*, tags, tag_times, linux_port, in_main, in_linux_port, now, override="", dry_run=False):
    """{"source", "tag", "commit", "reason"} for what to sync; source is "" when there is nothing to do.

    linux-port wins whenever main lacks its head. The direct sync then takes the newest release above
    linux-port's own that linux-port has lacked for more than LINUX_PORT_LAG_DAYS: each release has its
    own clock (`lag_clocks`), so a fresher tag neither syncs early nor postpones an overdue one. A release
    below one main already holds (a maintenance release) is never synced backwards.
    An override narrows the direct sync to the release it names and passes the same checks, so it never
    syncs what an unattended run could not. Only a dry run takes any release main lacks, as a preview."""
    def result(source, tag, commit, reason):
        return {"source": source, "tag": tag, "commit": commit, "reason": reason}

    releases = {tag: commit for tag, commit in tags.items() if version(tag)}
    if override:
        if not version(override):
            raise SyncError(f"tag override {override!r} is not a vX.Y.Z release tag")
        if override not in releases:
            raise SyncError(f"tag override {override} is not a tag of {UPSTREAM}")
        if in_main(releases[override]):
            return result("", "", "", f"main already contains {override}")
        if dry_run:
            return result(SOURCE_UMPUTUN, override, releases[override],
                          f"{override} was named by the dry run and is not an ancestor of main")
    if not releases:
        raise SyncError(f"no vX.Y.Z tags fetched from {UPSTREAM}")
    head = f"{LINUX_PORT_BRANCH} {linux_port[:8]}"
    if not in_main(linux_port):
        if override:
            return result("", "", "", f"{head} is not an ancestor of main and syncs before {override}")
        return result(SOURCE_LINUX_PORT, "", linux_port, f"{head} is not an ancestor of main")
    contained = newest_contained(releases, in_linux_port)
    if not contained:
        raise SyncError(f"{head} contains no {UPSTREAM} release")
    floor = max(contained, newest_contained(releases, in_main) or contained, key=version)
    pending = sorted((tag for tag in releases if version(tag) > version(floor)), key=version, reverse=True)
    if override:
        if override not in pending:
            return result("", "", "", f"{override} is not above {floor}, the newest release {head} and main carry")
        pending = [override]
    elif not pending and floor == contained:
        return result("", "", "", f"main contains {head}, which carries the newest release {contained}")
    elif not pending:
        return result("", "", "", f"main already contains {floor}")
    untimed = sorted(tag for tag in pending if tag not in tag_times)
    if untimed:
        raise SyncError(f"no lag clock for {', '.join(untimed)}")
    overdue = [tag for tag in pending if now - tag_times[tag] > LINUX_PORT_LAG_DAYS * DAY]
    tag = (overdue or pending)[0]
    lacked = f"{head} has lacked {tag} for {(now - tag_times[tag]) / DAY:.1f} days"
    if not overdue:
        return result("", "", "", f"{lacked}; the direct sync waits for more than {LINUX_PORT_LAG_DAYS}")
    return result(SOURCE_UMPUTUN, tag, releases[tag], f"{lacked}, more than {LINUX_PORT_LAG_DAYS}")


def settings_problems(workflow_permissions, secrets):
    """Why a sync push is unsafe under these repository settings; empty when it is safe.

    The pushed branch runs upstream-authored workflows before any review. That is acceptable only while
    their token is read-only and no repository secret exists for them to read."""
    if not isinstance(workflow_permissions, dict) or not isinstance(secrets, dict):
        return ["a repository settings response is not a JSON object"]
    problems = []
    default = workflow_permissions.get("default_workflow_permissions")
    if default != "read":
        problems.append(f"default workflow permissions are {default!r}, not 'read'")
    count, listed = secrets.get("total_count"), secrets.get("secrets")
    if type(count) is not int or not isinstance(listed, list):
        problems.append("the repository secret listing has no total_count and secrets")
    elif count or listed:
        names = ", ".join(str(secret.get("name")) if isinstance(secret, dict) else "?" for secret in listed)
        problems.append(f"repository secrets exist ({count}): {names}")
    return problems


def decide(*, paused_value, incidents, prs, remote_branches, **selection):
    """The run's decision. Gates in order: pause, one open sync PR, something to sync, no existing branch.

    `blocked` (a branch with no open PR) fails the run: skipping it green would hide a stuck sync forever."""
    plan = {"action": "skip", "source": "", "tag": "", "commit": "", "branch": "", "selection": ""}
    reason = pause_reason(paused_value, incidents)
    if reason:
        return {**plan, "reason": reason}
    chosen = select(**selection)
    reason, source = chosen["reason"], chosen["source"]
    if source:
        name = chosen["tag"] if source == SOURCE_UMPUTUN else chosen["commit"][:8]
        plan = {**plan, "source": source, "tag": chosen["tag"], "commit": chosen["commit"],
                "branch": BRANCH_PREFIXES[source] + name}
    plan["selection"] = reason
    blocking = open_sync_prs(prs)
    if blocking:
        numbers = ", ".join(f"#{pr.get('number')}" for pr in blocking)
        return {**plan, "reason": f"a sync PR is already open: {numbers}"}
    if not source:
        return {**plan, "reason": reason}
    if plan["branch"] in remote_branches:
        return {**plan, "action": "blocked",
                "reason": f"branch {plan['branch']} exists with no open sync PR; delete it to retry this sync"}
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


def pr_text(*, source, commit, release, pin, base_sha, head_sha, run_url, conflicts, github_changes):
    """(title, label, draft, body) for the sync PR.

    `release` is the (tag, commit) an umputun sync merges, or the newest one the linux-port head contains;
    `pin` is the newest one in the merge result, which main can already hold from an earlier direct sync.
    Any change under .github/ forces the draft: upstream's workflows run on this branch once it is pushed."""
    tag, pin_tag = release[0], pin[0]
    link = f"[{UPSTREAM} {tag}](https://github.com/{UPSTREAM}/releases/tag/{tag}) (`{release[1]}`)"
    if source == SOURCE_LINUX_PORT:
        title = f"Sync {LINUX_PORT} {LINUX_PORT_BRANCH} {commit[:8]} ({UPSTREAM} {tag})"
        merges = (f"Merges [{LINUX_PORT} `{LINUX_PORT_BRANCH}`](https://github.com/{LINUX_PORT}/commit/{commit}) "
                  f"(`{commit}`) into main (`{base_sha}`). This {LINUX_PORT_BRANCH} head contains {link}.")
    else:
        title = f"Sync {UPSTREAM} {tag}"
        merges = f"Merges {link} into main (`{base_sha}`)."
    files = " and ".join(f"`{path}`" for path in BASE_PIN_FILES)
    if pin_tag == tag:
        pins = (f"Before merging, bump `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to `{tag}` / `{pin[1]}` in both "
                f"{files}, unless main already pins them there.")
    else:
        pins = (f"main already contains the newer {UPSTREAM} {pin_tag}: keep `UPSTREAM_BASE_TAG` / "
                f"`UPSTREAM_BASE_COMMIT` at `{pin_tag}` / `{pin[1]}` in both {files}, and never move them back to "
                f"`{tag}`.")
    # The fields describe the run that built the branch; the reviewer replaces both with their own review.
    fields = [f"Task: {TASK}", "Agent: upstream-sync", f"Reviewed head: {head_sha}", f"Review: {run_url}", ""]
    body = fields + [
        merges,
        "Opened by `.github/workflows/upstream-sync.yml`. Merge as a plain merge commit after CI on both "
        "arches, a fresh-context review with no HIGH or MEDIUM, and the native gates on the merge result.",
        "The fields above name the run that built this branch, not a review: the reviewer replaces them.", "",
        pins, ""]
    if conflicts:
        body += [f"## Conflicts ({len(conflicts)})", "",
                 "The merge commit records these paths as committed with conflict markers (or the surviving side "
                 "of a delete conflict). Resolve them on this branch, adapt `agterm-linux`, set the base pins "
                 "as above, then mark the PR ready.", "",
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


def build(worktree, base, branch, commit, what):
    """Create `branch` in a new worktree as base merged with commit; conflicts are committed with markers.

    `what` names the merged source in the commit message.
    Returns (conflicts, paths the merge changes under .github/)."""
    git("worktree", "add", "-b", branch, str(worktree), base)
    message = f"Merge {what} into main"
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
    github = git("diff", "--name-only", "-z", base, "HEAD", "--", ".github", cwd=worktree).stdout
    return conflicts, sorted(path for path in github.split("\0") if path)


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
    plan_cmd.add_argument("--tag", default="", help=f"narrow the direct sync to this {UPSTREAM} release")
    plan_cmd.add_argument("--dry-run", default="", help="`true` lets --tag preview any release main lacks")
    plan_cmd.add_argument("--releases", required=True, type=Path,
                          help=f"gh api repos/{UPSTREAM}/releases as one JSON list of tag_name, draft, published_at")
    plan_cmd.add_argument("--first-seen", type=Path, help="the last run's first-seen record; a missing file is empty")
    plan_cmd.add_argument("--first-seen-out", type=Path, help="where to write the record the next run restores")
    plan_cmd.add_argument("--now", type=int, help="epoch seconds the lag is measured at; the clock by default")
    guard_cmd = sub.add_parser("guard", help="fail unless the repository settings make a sync push safe")
    guard_cmd.add_argument("--workflow-permissions", required=True, type=Path,
                           help="gh api repos/OWNER/REPO/actions/permissions/workflow")
    guard_cmd.add_argument("--secrets", required=True, type=Path, help="gh api repos/OWNER/REPO/actions/secrets")
    pause_cmd = sub.add_parser("pause", help="decide the pause alone, before anything is fetched")
    pause_cmd.add_argument("--paused", default="")
    pause_cmd.add_argument("--incidents", type=Path, help=f"gh issue list --state open --label {LABEL_INCIDENT} "
                           "--json number; without it, exit 3 when SYNC_PAUSED alone does not pause")
    merge_cmd = sub.add_parser("merge")
    for name in ("--base", "--branch", "--commit", "--worktree", "--body", "--run-url"):
        merge_cmd.add_argument(name, required=True)
    merge_cmd.add_argument("--source", required=True, choices=sorted(BRANCH_PREFIXES))
    merge_cmd.add_argument("--tag", default="", help=f"the {UPSTREAM} release an umputun sync merges")
    args = parser.parse_args(argv)
    try:
        if args.command == "pause":
            if args.incidents is None and not is_true(args.paused):
                return UNDECIDED
            incidents = [] if args.incidents is None else json.loads(args.incidents.read_text(encoding="utf-8"))
            reason = pause_reason(args.paused, incidents)
            emit({"paused": str(bool(reason)).lower(), "reason": reason})
        elif args.command == "plan":
            incidents = json.loads(args.incidents.read_text(encoding="utf-8"))
            prs = json.loads(args.open_prs.read_text(encoding="utf-8"))
            branches = {line.split("\t")[1].removeprefix("refs/heads/")
                        for line in args.remote_branches.read_text(encoding="utf-8").splitlines() if "\t" in line}
            base = git("rev-parse", "--verify", args.base + "^{commit}").stdout.strip()
            linux_port = git("rev-parse", "--verify", LINUX_PORT_REF + "^{commit}").stdout.strip()
            now = int(time.time()) if args.now is None else args.now
            seen = {}
            if args.first_seen and args.first_seen.exists():
                seen = json.loads(args.first_seen.read_text(encoding="utf-8"))
            tags = fetched_tags()
            clocks, kept = lag_clocks(tags, json.loads(args.releases.read_text(encoding="utf-8")), seen, now)
            plan = decide(paused_value=args.paused, incidents=incidents, prs=prs, remote_branches=branches,
                          tags=tags, tag_times=clocks, linux_port=linux_port, in_main=ancestor_check(base),
                          in_linux_port=ancestor_check(linux_port), now=now, override=args.tag.strip(),
                          dry_run=is_true(args.dry_run))
            if args.first_seen_out:
                args.first_seen_out.parent.mkdir(parents=True, exist_ok=True)
                args.first_seen_out.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            emit(plan)
        elif args.command == "guard":
            problems = settings_problems(json.loads(args.workflow_permissions.read_text(encoding="utf-8")),
                                         json.loads(args.secrets.read_text(encoding="utf-8")))
            if problems:
                raise SyncError("a sync push is unsafe: " + "; ".join(problems))
            print("repository settings allow a sync push")
        else:
            base = git("rev-parse", "--verify", args.base + "^{commit}").stdout.strip()
            tags = fetched_tags()
            if args.source == SOURCE_UMPUTUN:
                if not version(args.tag) or tags.get(args.tag) != args.commit:
                    raise SyncError(f"{args.tag!r} is not a fetched {UPSTREAM} tag at {args.commit}")
                tag, what = args.tag, f"{UPSTREAM} {args.tag}"
            else:
                tag = newest_contained(tags, ancestor_check(args.commit))
                if not tag:
                    raise SyncError(f"{LINUX_PORT_BRANCH} {args.commit[:8]} contains no {UPSTREAM} release")
                what = f"{LINUX_PORT} {LINUX_PORT_BRANCH} {args.commit[:8]}"
            conflicts, github_changes = build(Path(args.worktree), base, args.branch, args.commit, what)
            head = git("rev-parse", "HEAD", cwd=args.worktree).stdout.strip()
            pin = newest_contained(tags, ancestor_check(head))
            title, label, draft, body = pr_text(source=args.source, commit=args.commit, release=(tag, tags[tag]),
                                                pin=(pin, tags[pin]), base_sha=base, head_sha=head,
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
