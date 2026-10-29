# Omarchy development prereleases

`prerelease-omarchy.yml` promotes the exact tar archives from a successful Omarchy CI run.
It does not rebuild, modify the archives, change CI/UI gates, or publish on pull requests.
The inherited `release-linux.yml` remains separate and unchanged.

After reviewing and merging the promotion workflow, a maintainer dispatches it from `main` with:

```bash
gh workflow run prerelease-omarchy.yml --repo alexandru-savinov/agterm-omarchy --ref main \
  -f source_run=RUN_ID -f source_sha=FULL_TESTED_COMMIT_SHA
```

Dispatch performs publication, so review the exact source run and commit before executing this command.
The source must be a successful `Omarchy CI` run of `.github/workflows/ci.yml` in this repository,
triggered by push or manual dispatch on the current `main` or `omarchy` branch head.
PR runs are rejected because their tested merge commit can differ from the reported head commit.
Do not substitute a PR head SHA for its merge SHA or reuse older green artifacts to claim validation of newer fixes.
Run the final source through CI first; use manual CI dispatch on `omarchy` when it has not merged.

Promotion requires all core, lint, installer, aggregate CI and both native GTK jobs to pass.
It also checks successful Linux test, Release build, staged payload, GTK AT-SPI and upload steps.
Both artifacts must exist uniquely, be unexpired, match their API SHA-256 digests, contain matching source metadata,
and have the correct 64-bit ELF machine IDs for the app, CLI and zmx.
The tar paths and links must remain within `agterm-stage/`; nothing from the downloaded payload is executed.
The archives retain their `dev` version and original bytes.

The generated tag is `omarchy-dev-<12-character-SHA>-run<RUN_ID>`.
The workflow creates a new tag at the full tested SHA, then a prerelease with `latest=false`.
An existing tag or release is never overwritten.
If publication fails after tag creation, that tag remains for inspection; rerunning intentionally fails at tag creation.
Resolve the partial publication explicitly rather than deleting or force-updating tags automatically.

The release includes both architecture tarballs, `SHA256SUMS`, and `promotion.json` with source/run URLs and artifact identities.
GitHub's supported `actions/attest@v4` creates signed provenance for the promotion workflow and a separate signed promotion predicate.
The default provenance identifies the promotion workflow revision, not the original compiler invocation.
The additional predicate binds the unchanged archives to the verified build run and source SHA.
This is promotion provenance, not a retroactive attestation from the original compilation job.
Verify the archive with `gh attestation verify ARCHIVE --repo alexandru-savinov/agterm-omarchy`.
Inspect the custom predicate using `--predicate-type https://github.com/alexandru-savinov/agterm-omarchy/attestations/promotion/v1`.
See [GitHub's attestation action](https://github.com/actions/attest) for supported verification details.

Hosted tests run on Ubuntu with Xvfb.
The generated notes explicitly avoid claiming native Omarchy/Wayland validation of those exact artifacts.
Native acceptance evidence must separately identify its source and binaries.
The local ARM64 archive from issue #15 is a different artifact and is not automatically published by this workflow.
