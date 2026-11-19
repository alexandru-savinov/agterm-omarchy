# Omarchy development prereleases and versioned releases

`prerelease-omarchy.yml` promotes the exact tar archives from a successful Omarchy CI run.
It does not rebuild, change CI/UI gates, or publish on pull requests.
Without a version it publishes a development prerelease of unchanged archives (below).
With a version it publishes the versioned release that omarchy-pkgs follows ([Versioned releases](#versioned-releases)).
The inherited `release-linux.yml` (x86_64 linux-v releases) is retired here: all of its jobs are skipped in this repository.

A maintainer dispatches it from `main`. Dispatch is a dry run unless `dry_run=false` is passed:

```bash
gh workflow run prerelease-omarchy.yml --repo alexandru-savinov/agterm-omarchy --ref main \
  -f source_run=RUN_ID -f source_sha=FULL_TESTED_COMMIT_SHA            # dry run
gh workflow run prerelease-omarchy.yml --repo alexandru-savinov/agterm-omarchy --ref main \
  -f source_run=RUN_ID -f source_sha=FULL_TESTED_COMMIT_SHA -f dry_run=false  # publishes
```

A dry run executes every check that precedes attestation: source run and gates, tag grammar and absence of
that tag or release, artifact download and digests, archive and ELF checks, checksums and release notes.
It stops before any attestation, so it cannot run the attestation verification, nor create a tag or release;
its summary shows the tag and notes it would publish.
Pull requests touching the promotion or `ci.yml` dry-run the newest promotable source the same way, skipping
a run whose tag or release already exists; with no promotable source they warn that nothing was checked.
`python3 scripts/prepare-omarchy-prerelease.py --discover` prints that source locally.
Publication needs `dry_run=false`, so review the exact source run and commit before passing it.
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
Both attestations must pass `gh attestation verify` with the published flags before the tag is created.
This is promotion provenance, not a retroactive attestation from the original compilation job.
Verify the archive with `gh attestation verify ARCHIVE --repo alexandru-savinov/agterm-omarchy --signer-workflow alexandru-savinov/agterm-omarchy/.github/workflows/prerelease-omarchy.yml --source-ref refs/heads/main`.
Inspect the custom predicate using `--predicate-type https://github.com/alexandru-savinov/agterm-omarchy/attestations/promotion/v1`.
See [GitHub's attestation action](https://github.com/actions/attest) for supported verification details.

Hosted tests run on Ubuntu with Xvfb.
The generated notes explicitly avoid claiming native Omarchy/Wayland validation of those exact artifacts.
Native acceptance evidence must separately identify its source and binaries.
The local ARM64 archive from issue #15 is a different artifact and is not automatically published by this workflow.

## Versioned releases

omarchy-pkgs' `upstream-watch` follows non-prerelease releases whose whole tag matches
`omarchy-v(?P<version>[0-9]+(?:\.[0-9]+)*)`.
A version is the `UPSTREAM_BASE_TAG` of `ci.yml` plus a serial, `0.41.1.N` with N ≥ 1 and no leading zeros,
so pacman's vercmp orders it below upstream `0.41.2`; serial 0 is the CI rehearsal and is never published.
Dispatch from main is the only publishing path. Dry-run first, then publish the same source:

```bash
gh workflow run prerelease-omarchy.yml --repo alexandru-savinov/agterm-omarchy --ref main \
  -f source_run=RUN_ID -f source_sha=FULL_TESTED_COMMIT_SHA -f version=0.41.1.1            # dry run
gh workflow run prerelease-omarchy.yml --repo alexandru-savinov/agterm-omarchy --ref main \
  -f source_run=RUN_ID -f source_sha=FULL_TESTED_COMMIT_SHA -f version=0.41.1.1 -f dry_run=false
```

The workflow creates the tag itself, only after attestation verification; a hand-pushed `omarchy-v` tag triggers
nothing, and a later dispatch for that version refuses the existing tag.
The source must be a successful main push/dispatch CI run, its commit must be on main (main may have moved past it),
its CI artifacts must be unexpired (14 days), and the version's base must equal that commit's own `UPSTREAM_BASE_TAG`.
Every development-prerelease check applies, and then `scripts/omarchy-release.py` writes
`agterm-omarchy-<version>-<arch>.tar.gz` for both architectures: the tested archive with only
`share/agterm/VERSION` changed, numeric root ownership and a timestamp-free gzip header.
It rejects the archive unless both executables have RUNPATH `$ORIGIN/../lib`, no bundled ELF has an absolute
search path, `share/licenses/agterm/` carries the repository `LICENSE`, `payload.json` lists no unresolved
library, and every shipped theme is resolved, byte for byte, in the archive's own notice inventory;
`promotion.json` records the tested and released digest of each archive.
The source run must also have passed both arches' "Rehearse versioned release archive" step.
CI rehearses this on both architectures under serial 0 and checks the result natively with tar, readelf and ldd
(`release-rehearsal-<arch>-<sha>` artifacts).
The release is created as a non-prerelease; it is marked latest only when no published `omarchy-v` release
has a higher version, so a backport never takes it.
Attestations are verified with `--source-ref refs/heads/main`, as for development prereleases.
