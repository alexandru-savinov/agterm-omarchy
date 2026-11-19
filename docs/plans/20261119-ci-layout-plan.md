Restructure fork CI so umputun syncs never conflict on CI: byte-identical, disabled upstream ci.yml, plus our own omarchy-ci.yml and advisory omarchy-macos.yml.

## Context
The design is `docs/plans/20261119-ci-layout-design.md` (owner-approved 2026-11-19). It lands after #474 merges and
before #477/#478. Files: `.github/workflows/ci.yml`, `.github/workflows/macos-weekly.yml`,
the scanner workflows (`zizmor.yml`, `actionlint.yml`, `semgrep-osv.yml`, `trivy.yml`), `.claude/rules/ci.md`,
`README.md`, `scripts/upstream-sync.py`, `.github/workflows/upstream-sync.yml`, `.github/workflows/auto-release.yml`, and
`.github/workflows/prerelease-omarchy.yml`.

## Tasks

### Task 1: Split the pipelines
- [ ] `git mv .github/workflows/ci.yml .github/workflows/omarchy-ci.yml`, keeping the aggregate job name `CI`.
- [ ] Restore `.github/workflows/ci.yml` byte-identical from umputun tag `UPSTREAM_BASE_TAG`.
- [ ] Exclude `.github/workflows/ci.yml` in actionlint, zizmor, semgrep and trivy.
- [ ] Replay the v0.41.1→v0.43.0 merge on a scratch branch with the new layout, and record that ci.yml has no
  conflict.

### Task 2: Guard job
- [ ] Add `upstream-ci-disabled` to `omarchy-ci.yml`, under the aggregate `CI`, with `actions: read` only. It
  fails when an umputun-shipped workflow file (at `UPSTREAM_BASE_TAG`) is active, or differs from the tag.
- [ ] Put the logic in a script under `scripts/`, with unit tests in `scripts/tests` covering: active, edited,
  new upstream file, and clean.
- [ ] Make #476's sync branch bump `UPSTREAM_BASE_TAG` to the merged tag in the merge commit, with a test.

### Task 3: Advisory macOS workflow
- [ ] `git mv macos-weekly.yml omarchy-macos.yml`. Triggers: weekly, dispatch, and pull_request
  (opened/synchronize/labeled). The job runs when the PR is labelled `upstream-sync` or `upstream-offer`,
  touches `agtermCore/**`, or touches the workflow itself. Timeout 90 min.
- [ ] On failure or timeout, add the `macos-broken` label and a one-line comment linking the run. A later
  green run removes the label. In `upstream-offer` mode the job result is the verdict; otherwise the label
  step is the only output. It is never part of the required `CI`.
- [ ] Prove it with three test PRs: forced failure gets the label and is not blocked, `upstream-offer` shows
  red, and a green run removes the label.

### Task 4: References and enablement
- [ ] Update every lookup of `ci.yml` and `macos-weekly.yml`: upstream-sync, auto-release,
  prerelease-omarchy, `.claude/rules/ci.md`, the README badge and PR-fields docs.
- [ ] After merge, the lead runs `gh workflow disable ci.yml`. Show the guard red before and green after.
- [ ] Merge a docs-only PR under the unchanged ruleset, with `CI` green.

## Constraints
- Never change umputun's `ci.yml` content. Never change the required-check ruleset. Never make macOS
  required, except the `upstream-offer` verdict.
- No self-hosted runners. Pin actions by SHA, use least-privilege permissions, and pass zizmor and actionlint.
- PRs go to `main` on alexandru-savinov/agterm-omarchy only. Never push to melonamin or umputun.
- Never force-push, delete releases, or weaken a gate to pass it.
