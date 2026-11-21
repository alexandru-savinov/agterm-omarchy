# Omarchy landing page

This small static site is the public landing page for agterm for Omarchy:
what it is, how releases are cut and gated, the latest release and the README install steps,
the pinned [Status and roadmap](https://github.com/alexandru-savinov/agterm-omarchy/issues/525) issue,
Discussions, and the distance from upstream.
It does not name a release version, so it stays current between rebuilds.
`status.json` reports `readiness: "released"`, the releases URL, the status issue and the upstream base `v0.43.0`.

Build a local preview:

```sh
python3 docs/omarchy-pages/test_build.py
python3 docs/omarchy-pages/build.py --output /tmp/agterm-hub --commit "$(git rev-parse HEAD)"
python3 -m http.server 8080 --directory /tmp/agterm-hub
```

The page is script-free and loads no third-party assets.

Pass `--drift-run run.json --drift upstream-drift.json` to show how far `main` is from
`melonamin/agterm-linux:linux-port` (commits ahead, behind, and conflicting paths), with a link to the
measuring run of `upstream-drift.yml`. `run.json` is `gh run list --json databaseId,createdAt,event,headBranch`
output; only scheduled or dispatched runs on `main` count. A missing run or artifact renders a visible
"unavailable" notice, and a measurement older than 8 days a "stale" notice that keeps the numbers.
`status.json` carries the snapshot under `upstream_drift` and its state in `upstream_drift_status`
(`loaded`, `stale`, `unavailable` or `not-requested`).

The Pages workflow builds and tests PR previews, then deploys reviewed changes pushed to `main`.
Manual runs can turn off `deploy` for an artifact-only preview.
The hourly schedule rebuilds from `main` so a new drift measurement appears without a push.
Build artifacts are downloadable for 14 days. The deploy job uses the `github-pages` environment, records its URL,
and verifies the public JSON snapshot matches the deployed commit.

Repository Pages settings must use the GitHub Actions publishing source (`build_type: workflow`).
Only the deploy job receives Pages/OIDC write permissions; PR builds cannot deploy.
Keep `github-pages` restricted to `main` in repository environment settings.
The public page is `https://alexandru-savinov.github.io/agterm-omarchy/`.
