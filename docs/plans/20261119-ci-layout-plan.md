Move fork CI out of the inherited path so future linux-port syncs can update the disabled upstream workflow.

## Context
The design is `docs/plans/20261119-ci-layout-design.md` (owner-approved 2026-11-19). It lands after #474 merges and
before #478. The merged #477 gate depends on these CI attestations. Files: `.github/workflows/ci.yml`, `.github/workflows/macos-weekly.yml`,
the scanner workflows (`zizmor.yml`, `actionlint.yml`, `semgrep-osv.yml`, `trivy.yml`), `.claude/rules/ci.md`,
`README.md`, `scripts/upstream-sync.py`, `.github/workflows/upstream-sync.yml`, `.github/workflows/auto-release.yml`, and
`.github/workflows/prerelease-omarchy.yml`.

## Tasks

### Task 1: Split the pipelines
- [ ] `git mv .github/workflows/ci.yml .github/workflows/omarchy-ci.yml`, keeping the aggregate job name `CI`.
- [ ] Restore `ci.yml`, `release-linux.yml`, and `publish-aur.yml` byte-identical from `LINUX_PORT_BASE_COMMIT`.
- [ ] Exclude the three inherited paths in Actionlint, Zizmor, Semgrep and Trivy.
- [ ] Simulate an upstream change to all three. Require a clean merge and no new workflow paths.
- [ ] Replay the v0.41.1→v0.43.0 merge on a scratch branch with the new layout, and record that ci.yml has no
  conflict.

### Task 2: Guard job
- [ ] Add `upstream-ci-disabled` to `omarchy-ci.yml`, under the aggregate `CI`, with `actions: read` only. It
  checks the CI path's registry state and source blob, and fails on newly introduced upstream workflow paths.
- [ ] Put the logic in a script under `scripts/`, with unit tests in `scripts/tests` covering: active, edited,
  new upstream file, and clean.
- [ ] Make #476's sync branch advance `UPSTREAM_BASE_TAG` and `UPSTREAM_BASE_COMMIT` in `omarchy-ci.yml`
  in the merge commit, with a test. #493 also advances the linux-port source pin.

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
- [ ] After #474 merges, the lead pauses sync and disables all three inherited workflows.
  Show the draft guard red before and green after the disable.
- [ ] Verify both archives signed by `omarchy-ci.yml` on the first qualifying main run. Then require
  attestation in the native PR self-test and unpause.
- [ ] Merge a docs-only PR under the unchanged ruleset, with `CI` green.

## Constraints
- Preserve the linux-port workflow files byte for byte. Never change the required-check ruleset. Never make macOS
  required, except the `upstream-offer` verdict.
- No self-hosted runners. Pin actions by SHA, use least-privilege permissions, and pass zizmor and actionlint.
- PRs go to `main` on alexandru-savinov/agterm-omarchy only. Never push to melonamin or umputun.
- Never force-push, delete releases, or weaken a gate to pass it.
