# Omarchy coordination site

This small static site is the public landing page for agterm for Omarchy.
It leads with how releases are cut and gated and links to the latest release and the README install steps;
it does not name a release version, so it stays current between rebuilds.
Below that it keeps the historical pre-release acceptance records, the agent hub, the issue queue and upstream drift.
`status.json` reports `readiness: "released"`, the releases URL and the upstream base `v0.43.0`.

Build a local preview with a saved public issue snapshot:

```sh
python3 docs/omarchy-pages/test_build.py
printf '[]\n' > /tmp/agterm-issues.json
python3 docs/omarchy-pages/build.py --issues /tmp/agterm-issues.json \
  --output /tmp/agterm-hub --commit "$(git rev-parse HEAD)"
python3 -m http.server 8080 --directory /tmp/agterm-hub
```

The generator accepts a flat issue array or paginated `gh api --paginate --slurp` output.
It displays issue numbers, titles, and states; it ignores PRs and issue-supplied URLs, bodies, and HTML.
Titles are escaped and links are constructed from the fixed public repository and validated issue numbers.
No third-party assets are loaded.

The dedicated `team.html` page presents a reviewed ownership snapshot from `roster.json`.
It links prominently from the hub and uses self-hosted CSS/JavaScript with no network polling or tracking.
Its optional text, phase and workstream filters preserve the full readable roster when JavaScript is disabled.
The existing hub remains script-free.

The historical records distinguish the trusted matrix at `6775c336`, its exact ARM64 archive's seven ordinary
and nine Live native checks (ARM64 archive SHA256 prefix `2b629e40`), the earlier `fd85fc58` acceptance,
historical failed runs, and older installation/renderer evidence, with the conditions they ran under.
The 500-agent request remains distinct from actual launches.
The verified P09/A12 [inventory snapshot at `67e2d3bc`](https://github.com/alexandru-savinov/agterm-omarchy/blob/67e2d3bccc6e93b7844c1c4188bdbf63c968efed/packaging/linux/github-feature-evidence.md) records 72 proven, 45 pending and 4 unavailable features.

To refresh ownership, read the latest issue/PR comments before editing `roster.json`.
Record the observation time and each source's exact timestamp, agent/account, role, task and reported state.
An assignment without acknowledgment uses phase `assigned` and reported_state `null`.
Keep historical completed reports distinct from current work and review handoffs.
The November 11 refresh adds six acknowledged current roles ahead of the retained historical reports.
Old reports keep their original source timestamps and reported states; superseded active phases are historical.
These records describe acknowledged responsibilities, not verified execution or present process liveness.
Source paths accept only this repository's issue/PR references; text is escaped before HTML rendering.
The validated snapshot, including constructed source/task URLs, is published under `roster` in `status.json`.
Its `observed_at` is separate from the page build's `generated_at`; a rebuild does not refresh the reviewed evidence.

Pass `--queue /path/to/queue.json` to include the automatic queue snapshot.
The displayed phase, phase filter and phase totals use the latest queue claim for each account/name,
falling back to its curated phase when no matching claim exists.
Reviewed roles, work descriptions and historical state remain intact and labeled as curated.
Contributor totals include queue-only cards; each card identifies its own account.
Sharing an account is not a separate identity, and using another account is not evidence of independent approval.
Stale claims and snapshots remain visibly flagged; no queue observation proves process liveness or grants ownership.
Without a queue, the reviewed roster remains available.
If `--queue` names a missing or unreadable file, the page says the queue snapshot was unavailable and
`status.json` reports `queue_status: "unavailable"` (otherwise `loaded` or `not-requested`).
Only collaborator claims become cards; unverified claims are counted in a note, not shown as cards.

Pass `--drift-run run.json --drift upstream-drift.json` to show how far `main` is from
`melonamin/agterm-linux:linux-port` (commits ahead, behind, and conflicting paths), with a link to the
measuring run of `upstream-drift.yml`. `run.json` is `gh run list --json databaseId,createdAt,event,headBranch`
output; only scheduled or dispatched runs on `main` count. A missing run or artifact renders a visible
"unavailable" notice, and a measurement older than 8 days a "stale" notice that keeps the numbers.
`status.json` carries the snapshot under `upstream_drift` and its state in `upstream_drift_status`
(`loaded`, `stale`, `unavailable` or `not-requested`).

This display reconciliation adopts the tested proposal by `codex_team_pages` in
[PR #84](https://github.com/alexandru-savinov/agterm-omarchy/pull/84), following
[`codex_pages_review`'s #79 findings](https://github.com/alexandru-savinov/agterm-omarchy/issues/79#issuecomment-6527014289)
on `sancta`'s queue integration in [PR #76](https://github.com/alexandru-savinov/agterm-omarchy/pull/76).
Its regressions now run with the normal Pages tests; the original proposal remains under `docs/evidence/pages-queue-followup/`.

The Pages workflow builds and tests PR previews, then deploys reviewed changes pushed to `main`.
Manual runs can refresh the task snapshot and choose 10, 25, 50, or 100 tasks; turn off `deploy` for an artifact-only preview.
The builder, CLI and automatic/manual workflow defaults are 100 tasks.
This bounded snapshot includes the 59 open tasks observed during the November 11 audit; it is not unlimited coverage.
The existing hourly schedule refreshes the optional queue and deploys from main; issue changes do not themselves trigger publication.
Every page shows its snapshot time and links to live issues.
Build artifacts are downloadable for 14 days. The deploy job uses the `github-pages` environment, records its URL,
and verifies the public JSON snapshot matches the deployed commit.

Repository Pages settings must use the GitHub Actions publishing source (`build_type: workflow`).
Only the deploy job receives Pages/OIDC write permissions; PR builds cannot deploy.
Keep `github-pages` restricted to `main` in repository environment settings.
The public page is `https://alexandru-savinov.github.io/agterm-omarchy/` once deployment succeeds.
