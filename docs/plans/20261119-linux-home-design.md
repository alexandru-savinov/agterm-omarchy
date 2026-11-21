# agterm-omarchy as the maintained Linux home: design

Status: approved by the owner on 2026-11-19 (brainstorming session; decisions below are the owner's).
Milestone: M4 "Linux home: track umputun, auto-release". Related: #380 (fork gate), M3 (#461–#467).

## Context

- melonamin/agterm-linux `linux-port` (the Linux GUI port this fork descends from) has had no push since
  2026-11-08. Its only channel, PR #61, has had no reply since 2026-11-13. The repo has Issues disabled.
  melonamin is active elsewhere on GitHub.
- umputun/agterm (the app, MIT) ships fast: v0.42.0–v0.42.3 and v0.43.0 between 2026-11-12 and 2026-11-17. It
  merged our umputun/agterm#1053 on 2026-11-18. Its Linux direction is `agterm-server`, with no Linux GUI.
- This fork is "0 behind linux-port", but linux-port is frozen on umputun v0.41.1. At design time the fork was
  142 umputun commits (839 files) behind v0.43.0, and a trial merge had 49 conflicted files, mostly agtermCore
  and agtermctlKit.
- M3 lists the package `agterm-omarchy-bin` in omacom/omarchy-pkgs, watching *this* fork's `omarchy-v*`
  releases. Once listed, Omarchy users depend on this fork.
- Amended 2026-11-20 (#493): melonamin resumed `linux-port` on 2026-11-19 and published `linux-v0.43.0`, which
  carries umputun v0.43.0. The owner decided the fork follows linux-port again: decisions 2 and 5, C2 and C5.

## Decisions (owner)

1. **Goal:** keep a maintained agterm GUI on Omarchy/Linux regardless of linux-port's future. This fork is that
   home.
2. **Base** (amended 2026-11-20, #493): follow melonamin's `linux-port` as the Linux upstream. It carries each
   umputun/agterm release plus the Linux adaptations, and this fork stays downstream of it. A umputun release is
   merged directly only as a fallback, once linux-port has lacked it for more than 7 days.
3. **Identity:** keep `io.github.melonamin.agterm`, `agterm-linux` and `agtermctl`, and credit umputun (app)
   and melonamin (Linux port). Revisit only if melonamin objects or ships his own releases.
4. **melonamin:** one short courtesy note on linux-port #61. It says we track umputun directly, keep his IDs
   and credit, list the app on omarchy-pkgs, and will merge his work back or step aside if he resumes.
5. **Cadence:** one sync PR at a time, opened automatically: per linux-port head, or per umputun release tag
   in the fallback.
6. **Releases:** automatic after a green sync. No per-release owner approval.
7. **Release gates:** Omarchy CI plus native Omarchy gates on **both** arches, on hosted runners, against the
   exact release archives. No dependency on the owner's Mac or VM.
8. **Failure handling:** retract within the 24-hour omarchy-pkgs pickup window by marking the release as a
   prerelease (the watch ignores prereleases), pause automation, then fix forward with `.N+1`. Never delete a
   release.
9. **First omarchy-pkgs release:** wait for `omarchy-v0.43.0.1` after the catch-up sync. Do not publish
   0.41.1.1.

This amends #380: Linux-generic fixes now live in this fork first and are *offered* to linux-port, instead of
being upstream-first.

## Components

### C1. Catch-up sync (v0.41.1 to v0.43.0), one time and manual

- A plain merge of umputun tag `v0.43.0` into a branch `sync/umputun-v0.43.0`. Resolve the conflicts, adapt
  the Linux frontend to core API changes, and keep every fork fix that umputun doesn't have.
- The downstream CI and `release-linux.yml` move `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to v0.43.0.
  After the layout cutover, the downstream CI file is `omarchy-ci.yml`.
  Release versions then derive `0.43.0.N`.
- Acceptance:
  - Omarchy CI is green on both arches.
  - The hosted macOS weekly build is green, because the Mac app is umputun's code and must still build.
  - The x86_64 native gates (#446 workflow) pass.
  - A fresh-context review finds no HIGH or MEDIUM.

### C2. Sync automation

`upstream-sync.yml` is scheduled every six hours at 00:17, 06:17, 12:17 and 18:17 UTC and also runs on dispatch.
Amended 2026-11-20 (#493) to follow linux-port:
- **Primary source:** `melonamin/agterm-linux` `linux-port`. When its head isn't an ancestor of main, create the
  branch `sync/linux-port-<sha8>` as main merged with that head.
- **Fallback:** a direct umputun sync, only when main already has linux-port's head. It takes the newest
  umputun `vX.Y.Z` tag above linux-port's own that linux-port has lacked for more than 7 days
  (`LINUX_PORT_LAG_DAYS` in `scripts/upstream-sync.py`), on `sync/umputun-<tag>`. Each tag has its own lag,
  so a fresher tag waits its own 7 days and exactly 7 days still waits. A tag below a release main already
  holds is never synced.
- **Lag clock:** the `published_at` of the tag's GitHub release. A tag with no published release starts at
  the first live run that sees it at its commit, so a tag moved to another commit starts again. That time
  is carried between runs in the `upstream-sync-first-seen` artifact, read only from runs on main. Git dates
  are never used: a lightweight tag pushed today carries its commit's date. A lost record restarts the
  clock, so the fallback can only be late, never early.
- linux-port goes first when both apply; the fallback takes a later run, after the linux-port PR merges.
- A dispatch may name a umputun tag. A live run then syncs only that tag, and only if an unattended run could:
  main has linux-port's head, the tag is above the newest release linux-port and main carry, and its lag is
  past. Only a dry run previews any tag main lacks.
- **Publish rehearsal:** dispatch upstream sync on main with `rehearsal=true`, `dry_run=false`, and no tag.
  The read-only job creates a local `rehearsal/linux-port-fixture` ref from the dispatched main commit plus one
  synthetic file. The normal guarded publisher uses the `upstream-sync` environment to open a
  `[rehearsal]` draft labelled `sync-rehearsal`, then closes it and deletes the per-attempt rehearsal branch.
  The ordinary planner, pause, first-seen record and blocked-issue path are outside the rehearsal.
  If the runner is lost before cleanup, use its summary and push log to identify the draft and exact rehearsal branch.
- If linux-port or the release list can't be fetched, the run fails. It never falls back on its own.
- If the merge conflicts, or changes anything under `.github/`, keep the conflicts as a commit and open a draft
  PR labelled `needs-adaptation`. Otherwise open a PR labelled `upstream-sync`. A merge that adds a new
  workflow path is not pushed at all (Trust, below).
- The merge commit pins the newest incorporated umputun release in `omarchy-ci.yml`.
  A linux-port sync also advances `LINUX_PORT_BASE_COMMIT` and carries the three inherited workflow files.
  The PR body lists the expected pins for conflict resolution.
- **Trust:** the sync branch is pushed before anyone reviews it, and the workflows it carries run on that
  push. A workflow file can request a write token for itself (`permissions:`), whatever the repository
  default is, so an upstream-authored workflow could push branches or create tags and releases here.
  - **New workflow paths are never pushed.** If the merge adds a file under `.github/workflows/` at a path
    main lacks, the run pushes no branch and opens no PR. It opens, or rewrites, the one `needs-adaptation`
    issue titled `Upstream sync blocked: new workflow file(s) from <source>@<sha8>`, which lists the paths,
    and it ends with a warning, not a failure. A human or agent vendors those workflows to main and
    disables them; the next run then syncs. A workflow main deleted and upstream left alone stays deleted
    and does not block.
  - **Existing workflow paths** keep the `needs-adaptation` draft, with the changed paths listed. A workflow
    that is disabled in this repository stays disabled when its content changes.
    At the #483 cutover, `ci.yml`, `release-linux.yml`, and `publish-aur.yml` take the exact linux-port
    copies. Each registered workflow is disabled; the guard permits an unregistered reusable-only workflow
    until GitHub registers it. The fork publishes via `prerelease-omarchy.yml`.
    A clean merge into an enabled workflow path can run upstream-authored steps when its triggers match.
  - **Settings this rests on,** verified on 2026-11-20: there are no repository secrets; the only credential
    is the PAT in the `upstream-sync` environment, which deploys from main only; allowed actions are a
    selected list; Actions cannot approve pull requests. The default workflow permissions are read, but
    that is only a default a workflow can raise for itself.
  - **Settings guard:** before it pushes, the publish job reads the default workflow permissions and the
    repository secret list, and fails if the default is not read or a secret exists. Reading them takes
    Administration and Secrets read, which the sync PAT is deliberately not given (least privilege). GitHub
    then answers 403 or 404, and the job warns that those settings are unverified and pushes anyway. Any
    other failure to read them stops the push. So today they are a manual check: re-verify them whenever
    repository settings or secrets change.
  - A cross-repository PR never counts as an open sync PR, whatever its label.
- Apply the pause rule in C4 before opening a sync PR.
- Only one sync PR is open at a time. Commits and tags that arrive while one is open are taken by the next run,
  after merge.
- Agent pickup: the lead (mac_lead) watches for `needs-adaptation` and red `upstream-sync` PRs, and starts an
  adaptation worker. Fully unattended agent pickup is out of scope for M4.
- Merge rule: CI green on both arches, a fresh-context review with no HIGH or MEDIUM, and the native gates
  (C3) green on the merge result. Merge as a plain merge commit.

### C3. Native gates on both arches, hosted

- The existing x86_64 gate workflow (#446: Arch container, Omarchy packages, headless Hyprland on vkms) gains
  an aarch64 job on `ubuntu-24.04-arm`. The gates are ordinary 7/7, Live 9/9, config probe 10/10, and a clean
  uninstall.
- New input mode: run against **CI artifacts of a given main run**, not only a published release. Release
  gating then happens before publishing.

### C4. Automatic release

A workflow, `auto-release.yml`, triggers on a successful Omarchy CI push run on main whose head is a merged
sync, or a fork fix since the last release.
- Gate: C3 on that run's attested artifacts for both arches. Skip while automation is paused.
- Then call the #470 promotion path (dispatch on main, `dry_run=false`, the next `0.<base>.N` version). That
  path requires the exact run attempt, artifact IDs, and archive SHA-256 digests that passed C3.
  It verifies attestations before tagging, records the automatic release run, and publishes a non-prerelease release.
  Wait for active promotion dispatches on main before choosing a serial.
- After publishing, a 24-hour watch: re-run the C3 gates against the **published** assets, plus an install
  smoke from the release URLs. The scheduled watch reads the promotion record to select automatic releases.
  On a confirmed gate or URL probe failure:
  - open an issue labelled `release-incident` using the workflow token with `issues: write`;
  - mark the release as a prerelease using the workflow token with `contents: write` in the retraction job.

Release automation is paused when `SYNC_PAUSED` is nonempty and differs from `false` after trimming whitespace
and ignoring case, or any open issue carries the
`release-incident` label. Only the owner changes the variable for a manual hold. Close the incident to
clear its hold. Automatic work resumes once no incident remains open and the manual switch is clear.
The post-release watch continues while automation is paused.
Missing native evidence is retried twice with doubling backoff. Exhaustion opens a non-pausing
`release-watch-unavailable` issue.

### C5. Courtesy note and re-entry

- Post the note (decision 4) on melonamin/agterm-linux#61.
- linux-port moved on 2026-11-19: it is now the sync source (C2). Offer our Linux fixes as PRs there.

## Error handling

- Sync conflicts: a draft PR plus the `needs-adaptation` label. No release until it's merged.
- A red gate: no release. If a release is already published, retract and pause.
- Runner capacity (arm or macOS): retry with doubling backoff, up to 6 tries, then open an issue. Never release
  on missing evidence.
- Never delete tags or releases, never force-push, and never weaken a gate to pass.

## Testing

- Unit tests for the sync source selection, including the lag fallback, the version derivation (`0.43.0.N`)
  and retraction-state handling.
  Dry-run mode for every workflow.
- An end-to-end proof:
  - a real linux-port head or umputun tag produces a sync PR, which merges and leads to a published release;
  - a forced gate failure in a dry run exercises retract-and-pause;
  - The manual switch and an open release incident each block new syncs and promotions.

## Out of scope

Unattended agent pickup of `needs-adaptation`, Flatpak/AUR channels, macOS releases, and renaming the app ID.
