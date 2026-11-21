# CI layout that survives umputun syncs: design

Status: approved by the owner on 2026-11-19, in a brainstorming session.
Milestone: M4. Parent design: `docs/plans/20261119-linux-home-design.md`.
Source amendment on 2026-11-20: #474 and #493 track the `linux-port` branch of
`melonamin/agterm-linux`. The three preserved workflows come from that source.

## Problem

- umputun/agterm ships exactly one workflow, `.github/workflows/ci.yml`. He changed it in 3 of his 5 releases
  from v0.41.1 to v0.43.0.
- The fork's `ci.yml` is a different pipeline: Linux core and GTK on both arches, plus patch tests. It differs
  from his by about 500 lines, so a sync merge would conflict on `ci.yml` almost every time. Each such sync
  then needs manual adaptation, and C2 (the automatic sync) stalls.
- umputun's macOS checks (unit tests, app build, app-hosted tests) run on scarce hosted runners. The fork
  doesn't ship a Mac app, but it modifies the shared core and offers fixes back to umputun.

## Decisions (owner)

1. The macOS checks exist to protect fixes we offer upstream. They run on sync PRs, on PRs touching
   `agtermCore/**`, and weekly.
2. They are advisory: a failure or timeout labels the PR and never blocks a merge. The exception is a PR
   labelled `upstream-offer`, which needs a green macOS run before anything goes to umputun.
3. Layout A: the sync source's three workflow files stay byte-identical and disabled through the API. The fork's pipeline
   moves to its own file.

## Components

### 1. `omarchy-ci.yml`, the fork pipeline

- Move the fork's Linux checks from `.github/workflows/ci.yml`.
- Keep the aggregate job's name as `CI`, so the "Required CI on main branch" ruleset (`CI`, `Secret scan (full
  history)`, `Vulnerability and misconfig scan`, `Dependency review`) needs no change.

- `build-linux` uploads the tested tarballs with read-only permissions.
  A separate `attest-linux` matrix downloads and signs both archives on main pushes and manual dispatches.
  Release selection requires a successful signing step for each architecture.
  Native gating verifies the `omarchy-ci.yml` signer, main ref and source commit against both artifacts.

### 2. linux-port workflows, byte-identical and disabled

- Restore it from `LINUX_PORT_BASE_COMMIT` in `omarchy-ci.yml`. The #474 sync source is
  `12e8b468f607ffa99254648c1b701fc7e673f617`, which includes umputun v0.43.0.
- Preserve `ci.yml`, `release-linux.yml`, and `publish-aur.yml` exactly.
  The lead disables each registered workflow. Their registry states persist across updates.
- Actionlint, Zizmor, Semgrep and Trivy exclude these three guarded paths.
- The fork publishes through `prerelease-omarchy.yml` and `auto-release.yml`.

### 3. A guard job in `omarchy-ci.yml`

`upstream-ci-disabled` is part of the aggregate `CI` job. It uses the GitHub token with `actions: read` only.
It fails when:

- An inherited workflow registration is missing or its state differs from `disabled_manually`.
- An inherited workflow differs from the corresponding pinned linux-port blob.
- An upstream workflow appears outside the three reviewed paths.

The guard compares the workflow inventories at both incorporated source commits.
Sync merges advance `UPSTREAM_BASE_TAG` and `UPSTREAM_BASE_COMMIT` in `omarchy-ci.yml`.
The linux-port sync in #493 also advances `LINUX_PORT_BASE_COMMIT` to the exact source merged.
An upstream change under `.github/` is forced to `needs-adaptation` (#476).

### 4. `omarchy-macos.yml`, advisory macOS checks

- Rename `macos-weekly.yml`. It runs umputun's macOS set on `macos-26`: make build, make test,
  make test-app, make lint.
- Triggers:
  - weekly;
  - `workflow_dispatch`;
  - `pull_request` for PRs labelled `upstream-sync` or `upstream-offer`, for PRs touching `agtermCore/**`, and
    for PRs touching the workflow itself. The `labeled` event is included so that adding a label starts a run.
- It is not part of the required `CI` check.
- On failure or timeout (90 minutes), a separate trusted reporter adds the label `macos-broken` and posts
  a one-line comment linking the run. A later green run on the current head removes the label.
  The reporter uses the completed run and open PR metadata to handle fork PRs.
- `upstream-offer` mode: the job's result is the real verdict. The lead sends nothing to umputun without a
  green run on that exact head.

## Rollout

- The layout lands after #474 and supplies the attested archives required by the merged #477 gate and #478.
- The PR updates every reference to the old names:
  - `.claude/rules/ci.md`, the README badge and PR-fields docs;
  - every workflow or script that looks up CI runs by file name: `upstream-sync`, `auto-release`, and the
    `prerelease-omarchy` artifact lookup.
- After #474 lands, the lead keeps `SYNC_PAUSED=true`, disables all three inherited workflow IDs,
  and reruns the failed checks before merging. The required-check ruleset stays intact.
- The lead verifies both signed archives from the first qualifying main run before unpausing.
  Switch the native self-test to required once the attestations have been verified.

## Verification

- **No conflict:** replay the linux-port v0.41.1→v0.43.0 sync merge onto the new layout in a scratch branch. `ci.yml`
  merges with no conflict. Simulate a linux-port bump touching all three paths and verify there are no
  conflicts and no new workflow paths.
- **Guard:**
  - Red while any inherited workflow is active, green after all three are disabled.
  - A scratch PR that edits the inherited `ci.yml` turns `CI` red.
  - A scratch PR that adds a workflow file under an upstream name turns `CI` red.
- **Ruleset:** a docs-only PR merges with `CI` green under the unchanged ruleset.
- **macOS:**
  - A PR touching `agtermCore` gets the run.
  - A forced failure adds `macos-broken` and does not block the merge.
  - The same PR labelled `upstream-offer` shows the check red.
- **Lookups:** dry runs of `upstream-sync`, `auto-release` and `prerelease-omarchy` still find CI runs by
  the new file name.

## Out of scope

Self-hosted macOS runners (this is a public repo, and fork PRs would run on the owner's Mac), Codecov, and
changing the required-check ruleset.
