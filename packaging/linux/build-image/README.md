# Omarchy Linux build image

This is a reusable **x86_64 build/test environment**, separate from the client runtime archives.
It pins the official Swift 6.3.2 Ubuntu 24.04 base by manifest digest and verifies downloaded
Zig 0.16.0 and SwiftLint 0.65.0 using the same SHA-256 values as Omarchy CI.
GTK4, libadwaita, WebKitGTK 6, software GL, Xvfb and AT-SPI dependencies match the native x86_64 CI job.
Ubuntu dependency packages resolve from current repositories when built; they are not version locked.
The immutable published digest identifies the resulting package versions.
The base digest was verified against [Docker Hub's official Swift metadata](https://hub.docker.com/v2/repositories/library/swift/tags/6.3.2-noble).

The context is only this directory, and `.dockerignore` admits just `Dockerfile`, itself, and `probe.sh`.
There are no repository sources, user files, build secrets, tokens, or runtime binaries in the image.
OCI source/URL/base labels are declared in the Dockerfile; the workflow adds exact revision and version labels.
Run the image as a build container with an explicit source bind mount when needed.

After review/merge and the execution handoff, dispatch `publish-build-image.yml` on `main`.
Only the publish job has package-write permissions.
It uses Docker CLI and GitHub-owned checkout/attestation actions with the ephemeral `GITHUB_TOKEN`.
The package is `ghcr.io/alexandru-savinov/agterm-omarchy-build-x86_64`.
Each tag is unique to the full workflow source SHA, run ID and attempt; no `latest` or other moving tag is published.
Do not retry publication by reusing or overwriting a tag.

The build runs the embedded compiler/dependency probes before publishing.
A fresh authenticated job then pulls by digest and runs the probes with no network, a read-only root filesystem,
and a temporary writable `/tmp`.
A second fresh job repeats the pull and probes with an empty Docker config and no registry credentials.
Only that second successful pull proves public consumption.
The image receives GitHub's signed build provenance linked to its OCI digest.

GitHub defaults new GHCR packages to **private**, even when the linked repository is public.
The anonymous job deliberately fails with its registry error until public access works.
Use the package settings page to change this new package's visibility to public when authorized.
Then rerun only the failed anonymous job; that does not rebuild, republish, or replace a version.
If no authorized UI/API path is available, report the precise visibility gate and retain authenticated test evidence.
Never infer public access from the visibility setting alone.

After publication, substitute the recorded digest:

```bash
docker pull ghcr.io/alexandru-savinov/agterm-omarchy-build-x86_64@sha256:DIGEST
docker run --rm --network none --read-only --tmpfs /tmp:exec,size=1g \
  ghcr.io/alexandru-savinov/agterm-omarchy-build-x86_64@sha256:DIGEST agterm-build-image-probe
gh attestation verify oci://ghcr.io/alexandru-savinov/agterm-omarchy-build-x86_64@sha256:DIGEST \
  --repo alexandru-savinov/agterm-omarchy
```

Official references: [GHCR authentication and defaults](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[package visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility),
and [container provenance attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
