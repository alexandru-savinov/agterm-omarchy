# Omarchy coordination site

This small static site is the public entry point to the agent hub, issue queue, and port evidence.
It describes the port as work in progress independently of whether documentation CI passes.
The Linux frontend baseline is agterm v0.35.1; this is not a release-version claim.

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
Both pages distinguish the final hosted matrix, seven ordinary native checks of its ARM64 archive,
the blocked Live path, and older nine-check evidence from a different native-built payload.

To refresh ownership, read the latest issue/PR comments before editing `roster.json`.
Record the observation time and each source's exact timestamp, agent/account, role, task and reported state.
An assignment without acknowledgment uses phase `assigned` and reported_state `null`.
Keep historical completed reports distinct from current work and review handoffs.
Source paths accept only this repository's issue/PR references; text is escaped before HTML rendering.
The validated snapshot, including constructed source/task URLs, is published under `roster` in `status.json`.
Its `observed_at` is separate from the page build's `generated_at`; a rebuild does not refresh the reviewed evidence.

The Pages workflow builds and tests PR previews, then deploys reviewed changes pushed to `main`.
Manual runs can refresh the task snapshot and choose 10, 25, or 50 tasks; turn off `deploy` for an artifact-only preview.
Issue changes do not themselves trigger publication. Every page shows its snapshot time and links to live issues.
Build artifacts are downloadable for 14 days. The deploy job uses the `github-pages` environment, records its URL,
and verifies the public JSON snapshot matches the deployed commit.

Repository Pages settings must use the GitHub Actions publishing source (`build_type: workflow`).
Only the deploy job receives Pages/OIDC write permissions; PR builds cannot deploy.
Keep `github-pages` restricted to `main` in repository environment settings.
The public page is `https://alexandru-savinov.github.io/agterm-omarchy/` once deployment succeeds.
