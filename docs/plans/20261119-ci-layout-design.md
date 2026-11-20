# CI layout that survives umputun syncs: design

Status: approved by the owner on 2026-11-19, in a brainstorming session.
Milestone: M4. Parent design: `docs/plans/20261119-linux-home-design.md`.
Source amendment on 2026-11-20: #474 and #493 track the `linux-port` branch of
`melonamin/agterm-linux`. The preserved `ci.yml` comes from that source.

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
3. Layout A: the sync source's `ci.yml` stays byte-identical and is disabled through the API. The fork's pipeline
   moves to its own file.

## Components

### 1. `omarchy-ci.yml`, the fork pipeline

- Move the fork's Linux checks from `.github/workflows/ci.yml`.
- Keep the aggregate job's name as `CI`, so the "Required CI on main branch" ruleset (`CI`, `Secret scan (full
  history)`, `Vulnerability and misconfig scan`, `Dependency review`) needs no change.

- Each `build-linux` matrix leg attests the tested tarball it uploaded on a main push.
  Native gating verifies the `omarchy-ci.yml` signer, main ref and source commit against both artifacts.

### 2. linux-port's `ci.yml`, byte-identical and disabled

- Restore it from `LINUX_PORT_BASE_COMMIT` in `omarchy-ci.yml`. The #474 sync source is
  `12e8b468f607ffa99254648c1b701fc7e673f617`, which includes umputun v0.43.0.
- The lead disables it with `gh workflow disable ci.yml`. The registry state persists across updates to the file.
- Actionlint, Zizmor, Semgrep and Trivy exclude only this inherited workflow path.
- The source also ships `release-linux.yml` and `publish-aur.yml`.
  The fork keeps its retired release workflow with its fork-specific skip gates.
  Upstream AUR publishing stays out of the fork.

### 3. A guard job in `omarchy-ci.yml`

`upstream-ci-disabled` is part of the aggregate `CI` job. It uses the GitHub token with `actions: read` only.
It fails when:

- The registered `ci.yml` is missing or its state differs from `disabled_manually`.
- The checked-in `ci.yml` differs from the pinned linux-port blob.
- An upstream workflow appears outside the three reviewed paths, or AUR publishing appears in the fork.

The guard compares the workflow inventories at both incorporated source commits.
Sync merges advance `UPSTREAM_BASE_TAG` and `UPSTREAM_BASE_COMMIT` in `omarchy-ci.yml` and `release-linux.yml`.
The linux-port sync in #493 also needs to advance `LINUX_PORT_BASE_COMMIT` to the exact source merged.
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
- After #474 lands, the lead disables `ci.yml` and reruns this PR's required guard before merging.
  The path shares the current pipeline's GitHub registration, so the disable and merge are a coordinated
  cutover. Keep the required-check ruleset intact.
- Verify both CI archives from the first qualifying main push, then switch the native self-test to required.

## Verification

- **No conflict:** replay the linux-port v0.41.1→v0.43.0 sync merge onto the new layout in a scratch branch. `ci.yml`
  merges with no conflict. Then run a dry-run sync (#476) against the next umputun tag once one exists.
- **Guard:**
  - Red before `gh workflow disable`, green after.
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
