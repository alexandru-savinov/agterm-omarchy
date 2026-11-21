# Draft pull request for omacom/omarchy-pkgs

Open it as a draft: a non-draft PR that only touches packages merges itself once green.
`#3184` is the package request, already posted. Nothing in this file has been posted.

**Title:** `Add agterm-omarchy-bin`

---

Adds `agterm-omarchy-bin`, as asked in #3184: a prebuilt package of the agterm terminal (libghostty, a workspace and session sidebar, Live sessions, the `agtermctl` control CLI) for x86_64 and aarch64.

The app is umputun's [agterm](https://github.com/umputun/agterm) (MIT), and the GTK4/libadwaita Linux port is melonamin's [agterm-linux](https://github.com/melonamin/agterm-linux). The package builds from releases of my fork, [agterm-omarchy](https://github.com/alexandru-savinov/agterm-omarchy), which adds aarch64 and a launcher-scoped retry for GTK's EGL config request on Mesa 26.3.1 (Omarchy edge, [GNOME/gtk#8566](https://gitlab.gnome.org/GNOME/gtk/-/work_items/8566)).

**Package**

- Installs the [`omarchy-v0.43.0.2`](https://github.com/alexandru-savinov/agterm-omarchy/releases/tag/omarchy-v0.43.0.2) archives, with the sha256 values from its `SHA256SUMS`, to `/usr/lib/agterm`. It adds `/usr/bin/agterm-linux` and `/usr/bin/agtermctl` symlinks, the desktop entry, the icons and the license.
- Extracts the archive with `bsdtar --no-same-owner`. The Swift runtime, libghostty and a private zmx ship as CI built and tested them (`!strip !debug`), and the bundled terminfo and Ghostty resources stay under `/usr/lib/agterm`.
- `depends` are the owners of the binaries' `NEEDED` libraries outside the archive's own `lib/`.
- `provides=(agterm-linux)` and `conflicts=(agterm-linux agterm-linux-bin)`, because AUR `agterm-linux-bin` installs the same commands. Neither name is in OPR, so there is no `approved` line.
- Upstream watch on `omarchy-v` GitHub Releases with `min_release_age` set to `24h`. Releases are cut automatically after native gates on both architectures, each with a build-provenance attestation. A release that fails its 24-hour re-gate becomes a prerelease, which the watch skips.
- The `release/owners.json` line was written by `bin/release-owners agterm-omarchy-bin`.

**Tested** ([evidence](https://github.com/alexandru-savinov/agterm-omarchy/pull/468#issuecomment-7061198535))

- `bin/build` at `5c7d21d2` on `ubuntu-24.04` and `ubuntu-24.04-arm` ([run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/44893403760)):
  - every file is owned by root;
  - namcap reports no errors on the package or the PKGBUILD;
  - `ldd -r` resolves everything, the highest glibc symbol is `GLIBC_2.38`, and `desktop-file-validate` passes.
- `tests/pinned-sources.sh`, `tests/pkgname-unique.sh`, `bin/check-shadowed` and `bin/release-owners --check` pass, and `helpers/upstream-watch.py check` reports "already current".
- On an aarch64 Omarchy VM (Arch Linux ARM, Hyprland 0.57.2, Mesa 26.2.7):
  - `pacman -U` installed it without pulling in any new package;
  - through `/usr/bin`, the app passed 7/7 ordinary and 9/9 Live-session checks under Hyprland with software rendering;
  - `pacman -R` removed every file.
- The release's own native gates passed on x86_64 and aarch64 against the stable channel (Mesa 26.2.5) ([run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/44882977083)). The EGL retry passed the same gates on edge (Mesa 26.3.1) at an earlier commit with the same retry code ([run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/44647147715)).
- Not tested: hardware GL rendering, and an x86_64 Omarchy install.

It's a draft so you can look before it merges itself; I'll mark it ready when you're happy with it.

Prepared with AI assistance ([Claude Code](https://claude.com/claude-code)) and reviewed by me before posting.

---

## Files and branch plan (for mac_lead, not posted)

The PR changes exactly three files on top of omarchy-pkgs `master`:

| File | Change | Source here |
|---|---|---|
| `pkgbuilds/agterm-omarchy-bin/PKGBUILD` | added | `packaging/omarchy-pkgs/agterm-omarchy-bin/PKGBUILD` |
| `pkgbuilds/agterm-omarchy-bin/.omarchy/package.json` | added | `packaging/omarchy-pkgs/agterm-omarchy-bin/.omarchy/package.json` |
| `release/owners.json` | one line added, between `aether` and `alsa-ucm-conf` | `packaging/omarchy-pkgs/owners.json.line` |

- **Fork and branch:**
  - fork `omacom/omarchy-pkgs` to `alexandru-savinov/omarchy-pkgs`, which doesn't exist yet;
  - branch `add-agterm-omarchy-bin` from upstream `master` at post time.
- **Base commits:** the checks ran at `5c7d21d2af2fa1bea38b5c0dffeda4dfe40a6ab6`, and `master` was `76f5f2533a0dc25efeaccefec8abcfed4ccf5967` on 2026-11-21. Nothing in `bin/`, `helpers/`, `tests/`, `build/` or `.github/` changed between them.
- **`release/owners.json`:** omarchy-pkgs' `AGENTS.md` says never to edit the directory lines by hand. Run `bin/release-owners agterm-omarchy-bin` on Arch, as a non-root user, and check that it adds exactly `owners.json.line`. This repository's `omarchy-pkgs recipe build` workflow does this on every run.

```bash
gh repo fork omacom/omarchy-pkgs --clone=false
git clone https://github.com/alexandru-savinov/omarchy-pkgs.git && cd omarchy-pkgs
git remote add upstream https://github.com/omacom/omarchy-pkgs.git && git fetch upstream
git switch -c add-agterm-omarchy-bin upstream/master
AGTERM=~/src/agterm-omarchy   # any checkout that has fetched claude/omarchy-pkg-465
mkdir -p pkgbuilds/agterm-omarchy-bin/.omarchy
for f in PKGBUILD .omarchy/package.json; do
  git -C "$AGTERM" show 56ee67ae:packaging/omarchy-pkgs/agterm-omarchy-bin/$f > pkgbuilds/agterm-omarchy-bin/$f
done
bin/release-owners agterm-omarchy-bin   # on Arch, non-root
git diff release/owners.json            # exactly the line in owners.json.line
tests/pinned-sources.sh && tests/pkgname-unique.sh
python helpers/upstream-watch.py check pkgbuilds/agterm-omarchy-bin   # "already current"
git add pkgbuilds/agterm-omarchy-bin release/owners.json && git commit -m "Add agterm-omarchy-bin"
git push -u origin add-agterm-omarchy-bin
gh pr create --draft -R omacom/omarchy-pkgs --base master \
  --head alexandru-savinov:add-agterm-omarchy-bin --title "Add agterm-omarchy-bin" --body-file pr-body.md
```

- **Recipe bytes:** the two recipe files last changed at `56ee67ae` (0.43.0.2), which CI run 44893403760 and the VM tested; the commands copy them from that commit.
- **PR body:** `pr-body.md` is everything between the two `---` rules above.
- **A newer release:** if an `omarchy-v` release newer than 0.43.0.2 is out at post time, `upstream-watch` no longer reports "already current". Bump `pkgver`, take the sha256 values from that release's `SHA256SUMS`, and rerun the checks before posting.
- **Build approval:** `alexandru-savinov` isn't in `.github/VOUCHED.td` or a collaborator, so the PR shows **Awaiting build approval** until a maintainer labels it `build-approved`.
- **Auto-merge:** marking the PR ready for review, once it's trusted and green, arms auto-merge and publishes to `edge`.
