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

## Decisions (owner)

1. **Goal:** keep a maintained agterm GUI on Omarchy/Linux regardless of linux-port's future. This fork is that
   home.
2. **Base:** track umputun/agterm releases directly. Merge every umputun release, and carry the Linux frontend
   as our own code. melonamin's linux-port becomes an ordinary contributor: if it moves again, we merge it.
3. **Identity:** keep `io.github.melonamin.agterm`, `agterm-linux` and `agtermctl`, and credit umputun (app)
   and melonamin (Linux port). Revisit only if melonamin objects or ships his own releases.
4. **melonamin:** one short courtesy note on linux-port #61. It says we track umputun directly, keep his IDs
   and credit, list the app on omarchy-pkgs, and will merge his work back or step aside if he resumes.
5. **Cadence:** one sync PR per umputun release tag, opened automatically.
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
- `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` in ci.yml move to v0.43.0, and the boundary checks follow.
  Release versions then derive `0.43.0.N`.
- Acceptance:
  - Omarchy CI is green on both arches.
  - The hosted macOS weekly build is green, because the Mac app is umputun's code and must still build.
  - The x86_64 native gates (#446 workflow) pass.
  - A fresh-context review finds no HIGH or MEDIUM.

### C2. Sync automation

A scheduled workflow, `upstream-sync.yml`, runs daily and also on dispatch:
- Find the newest `umputun/agterm` tag `v*` that isn't an ancestor of main.
- Create the branch `sync/umputun-<tag>` as main merged with that tag. If the merge conflicts, keep the
  conflicts as a commit and open a draft PR labelled `needs-adaptation`. Otherwise open a PR labelled
  `upstream-sync`.
- Apply the pause rule in C4 before opening a sync PR.
- Only one sync PR is open at a time. Tags that arrive while one is open are taken by the next run, after merge.
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
  Wait for any active promotion workflow before choosing a serial.
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
- If linux-port moves: merge it like any contribution, and offer our Linux fixes as PRs there.

## Error handling

- Sync conflicts: a draft PR plus the `needs-adaptation` label. No release until it's merged.
- A red gate: no release. If a release is already published, retract and pause.
- Runner capacity (arm or macOS): retry with doubling backoff, up to 6 tries, then open an issue. Never release
  on missing evidence.
- Never delete tags or releases, never force-push, and never weaken a gate to pass.

## Testing

- Unit tests for the sync tag selection, the version derivation (`0.43.0.N`) and retraction-state handling.
  Dry-run mode for every workflow.
- An end-to-end proof:
  - a real umputun tag produces a sync PR, which merges and leads to a published release;
  - a forced gate failure in a dry run exercises retract-and-pause;
  - The manual switch and an open release incident each block new syncs and promotions.

## Out of scope

Unattended agent pickup of `needs-adaptation`, Flatpak/AUR channels, macOS releases, and renaming the app ID.
