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

A dry run executes the preparation checks: source run and gates, tag grammar and absence of that tag or release,
artifact download and digests, archive and ELF checks, checksums and release notes.
Its summary shows the tag and notes it would publish.
It skips the publishing job, including that job's transfer-artifact download/digest check and second checksum verification.
It therefore does not create or verify attestations, or create a tag or release.
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

## M4 automatic releases

`auto-release.yml` selects a successful Omarchy CI push run at the current main head.
It skips a commit already covered by the latest versioned release, including a retracted release.
The first upstream base is `0.43.0`.
The next serial is one above the highest exact tag or release for that base, including drafts, retractions,
and tags left behind after a failed publication.
Automatic releases wait while a promotion dispatched on main is active.
Automatic promotion requires #481's green attested verdict for both architectures.
It dispatches this workflow on main with `dry_run=false`, the gated source SHA, run ID and `source_attempt`,
the native gate's `archives` output as `native_archives`, and the automatic release workflow run ID.
Promotion checks the attempt and both artifact IDs and transport digests.
It hashes each downloaded CI tarball and requires an exact match to the SHA-256 that passed the native gates
before stamping the versioned release archive. Signed `promotion.json` retains the tested digests and
`auto_release_run_id`.
Both manual promotion modes retain their existing inputs.

Any nonempty repository variable `SYNC_PAUSED` other than `false`, ignoring case and surrounding whitespace,
sets a manual hold. An open issue labelled `release-incident`
also holds automation. Close resolved incidents and clear any manual hold to resume.
Automatic promotion checks both holds at candidate selection, before dispatch, and after attestation
verification before tagging. It reads the manual switch from Actions configuration and queries open
incidents with the built-in token. Gate runners receive read-only permissions.

The watch starts as soon as promotion reports success. Every two hours it also finds stable releases from
the last 24 hours whose promotion record identifies an automatic release run.
The #475 published-release mode downloads, verifies, and installs both architectures' published assets.
The watch also tests both public asset URLs without credentials.
The watch runs while automation is paused. A confirmed failing native gate or failed public URL probe opens
a `release-incident` issue to hold
new promotions and records the failed checks and run link there. It marks the release as a prerelease.
Both edits use the built-in Actions token, with write permissions scoped to the retraction job.
The release and tag remain available for investigation and the next fixed commit uses a new serial.
If any incident action fails, review the incident and rerun the failed watch jobs before resuming.
If native evidence is incomplete or unavailable, the watch retries twice, after 60 and 120 seconds.
If it still has no verdict, it opens a `release-watch-unavailable` issue. This label does not pause releases
and the release stays published. Scheduled starts depend on hosted Actions availability.

Both native gate calls use #481's reusable workflow in required attestation mode.
CI-artifact gating stays closed until #483 supplies verified build attestations for both architectures.
The post-release watch requires a green attested verdict for the requested release tag.
Manual dispatch of `auto-release.yml` runs only fixtures, even from main.
The **force_failure** input feeds a synthetic failed verdict through the same watch and retraction job.
The retraction code uses stubbed GitHub operations in the rehearsal. To run that part locally:

```bash
python3 -I scripts/tests/test_omarchy_auto_release.py --rehearse-retract
```
