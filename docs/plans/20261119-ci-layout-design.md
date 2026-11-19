# CI layout that survives umputun syncs: design

Status: approved by the owner on 2026-11-19, in a brainstorming session.
Milestone: M4. Parent design: `docs/plans/20261119-linux-home-design.md`.

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
3. Layout A: umputun's `ci.yml` stays byte-identical and is disabled through the API. The fork's pipeline
   moves to its own file.

## Components

### 1. `omarchy-ci.yml`, the fork pipeline

- Rename the current `.github/workflows/ci.yml` with `git mv`. The jobs don't change.
- Keep the aggregate job's name as `CI`, so the "Required CI on main branch" ruleset (`CI`, `Secret scan (full
  history)`, `Vulnerability and misconfig scan`, `Dependency review`) needs no change.

### 2. umputun's `ci.yml`, byte-identical and disabled

- Restore it from the umputun tag recorded in `UPSTREAM_BASE_TAG` (v0.43.0 after #474).
- Disable it with `gh workflow disable ci.yml`. That's repository state, and it survives later file changes,
  so sync merges apply his edits cleanly.
- actionlint, zizmor, semgrep and trivy exclude `.github/workflows/ci.yml`, since it never runs here.

### 3. A guard job in `omarchy-ci.yml`

`upstream-ci-disabled` is part of the aggregate `CI` job. It uses GITHUB_TOKEN with `actions: read` only. It
fails when any of these hold:

- A workflow file that umputun ships at `UPSTREAM_BASE_TAG` is active in this repository.
- A workflow file that umputun ships differs from its content at that tag.

So a sync that adds a new upstream workflow, or a hand edit to his file, turns `CI` red.
Every sync PR moves `UPSTREAM_BASE_TAG` (in `omarchy-ci.yml`) to the merged tag in the same commit, so the guard
compares against the tag actually merged; #476's sync branch does this as part of the merge. A sync that changes
`.github/` is already forced to `needs-adaptation` (#476).

### 4. `omarchy-macos.yml`, advisory macOS checks

- Rename `macos-weekly.yml`. It runs umputun's macOS set on `macos-26`: make build, make test,
  make test-app, make lint.
- Triggers:
  - weekly;
  - `workflow_dispatch`;
  - `pull_request` for PRs labelled `upstream-sync` or `upstream-offer`, for PRs touching `agtermCore/**`, and
    for PRs touching the workflow itself. The `labeled` event is included so that adding a label starts a run.
- It is not part of the required `CI` check.
- On failure or timeout (90 minutes per job), it adds the label `macos-broken` and posts a one-line comment
  linking the run. A later green run on the PR removes the label. A timeout counts as "not run", never as
  a pass.
- `upstream-offer` mode: the job's result is the real verdict. The lead sends nothing to umputun without a
  green run on that exact head.

## Rollout

- This lands after #474 (the catch-up) merges, and before #477/#478, so the first automatic release ships
  with this layout. It's a single PR.
- The PR updates every reference to the old names:
  - `.claude/rules/ci.md`, the README badge and PR-fields docs;
  - every workflow or script that looks up CI runs by file name: `upstream-sync`, `auto-release`, and the
    `prerelease-omarchy` artifact lookup.
- After merge, the lead runs `gh workflow disable ci.yml`. The guard is red on main until then, by design.

## Verification

- **No conflict:** replay the v0.41.1→v0.43.0 sync merge onto the new layout in a scratch branch. `ci.yml`
  merges with no conflict. Then run a dry-run sync (#476) against the next umputun tag once one exists.
- **Guard:**
  - Red before `gh workflow disable`, green after.
  - A scratch PR that edits umputun's `ci.yml` turns `CI` red.
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
