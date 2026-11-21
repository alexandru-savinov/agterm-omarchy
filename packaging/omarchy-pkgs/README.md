# omarchy-pkgs recipe draft

`agterm-omarchy-bin/` is the recipe for `pkgbuilds/agterm-omarchy-bin/` in
[omacom/omarchy-pkgs](https://github.com/omacom/omarchy-pkgs) (#465), and `owners.json.line` is the
`release/owners.json` line that `bin/release-owners agterm-omarchy-bin` generates for it on Arch.
Nothing here is submitted upstream without the owner's approval. `SUBMISSION-issue.md` and `SUBMISSION-pr.md`
hold the text of the package request issue and the draft PR (#467), and the PR's file and branch plan.

`.github/workflows/omarchy-pkgs-build.yml` builds it with omarchy-pkgs' own `bin/build` on both
architectures, inspects the package and runs that repository's recipe checks (#466).

The recipe builds the `omarchy-v` release named by `pkgver`; omarchy-pkgs' upstream watch moves it to each new
release after 24 hours.
