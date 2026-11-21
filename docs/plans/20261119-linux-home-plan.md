Make agterm-omarchy the maintained Linux home: track umputun releases, release automatically behind native gates.

## Context
Design: `docs/plans/20261119-linux-home-design.md` (owner-approved 2026-11-19). Milestone M4. The first
omarchy-pkgs release (M3) waits for `omarchy-v0.43.0.1` after Task 1.

## Tasks

### Task 1: Catch-up sync to umputun v0.43.0 (C1)
- [ ] On branch `sync/umputun-v0.43.0`, plain-merge umputun tag `v0.43.0` into main. Resolve the 49
  conflicted files (mostly agtermCore and agtermctlKit), and keep every fork fix that umputun doesn't have.
- [ ] Adapt agterm-linux to the core API changes. Move `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to
  v0.43.0, and update the boundary checks and tests.
- [ ] Gates: Omarchy CI green on both arches, the hosted macOS weekly build green, and the x86_64 native gates
  (#446) green.
- [ ] A fresh-context review with no HIGH or MEDIUM, then merge as one plain merge commit.

### Task 2: Native gates on aarch64, plus a CI-artifact mode (C3)
- [ ] Add an aarch64 job on `ubuntu-24.04-arm` to `.github/workflows/omarchy-x86-native.yml` (or a renamed
  successor). It runs the same ordinary, Live, probe and uninstall gates.
- [ ] Add an input mode that tests a given main CI run's artifacts, not only a published release.
- [ ] Prove it with green runs on both arches against main's current artifacts.

### Task 3: Sync automation (C2)
- [ ] Add `.github/workflows/upstream-sync.yml`, scheduled daily and on dispatch. It finds the newest
  umputun `v*` tag that isn't an ancestor of main, and opens `sync/umputun-<tag>`: labelled `upstream-sync`
  if the merge is clean, or as a draft labelled `needs-adaptation` with the conflicts committed.
- [ ] It keeps one open sync PR at a time and respects the C4 manual and incident holds.
- [ ] Unit tests for tag selection, plus a dispatch dry run.

### Task 4: Automatic release with retraction and pause (C4)
- [ ] Add `.github/workflows/auto-release.yml`. On a successful main push CI run, it runs Task 2's gates on
  that run's artifacts. When they're green and automation is unpaused, it calls the #470 promotion with
  `dry_run=false` and the next `<base>.N` version.
- [ ] Watch automatically promoted assets for 24 h. On a confirmed gate or public URL failure, open a
  `release-incident` issue to pause automation and mark the release as a prerelease using `GITHUB_TOKEN`.
  Retry missing native evidence and report exhaustion without pausing.
- [ ] Tests: version derivation, the pause gate, and retraction (dry-run with a forced failure).

### Task 5: First automated release, end to end
- [ ] After Tasks 1–4 merge, the first automatic release `omarchy-v0.43.0.1` publishes. Verify attestations,
  the release flags and the assets.
- [ ] Hand off to M3: the recipe points at it (#465), the native acceptance on its exact bytes runs (#464),
  and the package-request issue opens on omarchy-pkgs.

## Constraints
- Never force-push, never delete tags or releases, and never weaken a gate or timeout to make it pass.
- Main is protected: use PRs. mac_lead merges after a fresh-context review with no HIGH or MEDIUM.
- Before Task 4 merges, no release is published except by the owner.
- Never execute `agterm`/`agtermctl` against the default socket, never launch or quit the app, static reading
  only, except isolated Debug instances with their own state and socket.
- Hosted runner scarcity: retry with doubling backoff (max 6), then open an issue. Never release on missing
  evidence.
