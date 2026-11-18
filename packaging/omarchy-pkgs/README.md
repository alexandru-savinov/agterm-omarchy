# omarchy-pkgs recipe draft

`agterm-omarchy-bin/` is the recipe for `pkgbuilds/agterm-omarchy-bin/` in
[omacom/omarchy-pkgs](https://github.com/omacom/omarchy-pkgs) (#465), and `owners.json.line` is the
`release/owners.json` line that `bin/release-owners agterm-omarchy-bin` generates for it on Arch.
Nothing here is submitted upstream without the owner's approval.

`.github/workflows/omarchy-pkgs-build.yml` builds it with omarchy-pkgs' own `bin/build` on both
architectures, inspects the package and runs that repository's recipe checks (#466).

Until #462 publishes a non-prerelease `omarchy-v0.41.1.N` release, the recipe builds the prerelease
`omarchy-dev-b1356f0ad7ae-run43761095738` with the placeholder `pkgver=0.41.1.0`. Then:

- set `pkgver` to the release's version and drop `_tag` and its two `source_*` lines for the commented ones;
- run `updpkgsums`, so both `sha256sums_*` match that release's `SHA256SUMS`;
- `upstream-watch` stops reporting BLOCKED and becomes a gate.
