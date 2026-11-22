# Draft pull request for omacom/omarchy-pkgs

Open it as a draft: a non-draft PR that only touches packages merges itself once green.
`#3184` is the package request, already posted. Nothing in this file has been posted.

**Title:** `Add agterm-omarchy-bin`

---

I maintain `agterm-omarchy-bin`, requested in #3184: a prebuilt x86_64 and aarch64 package of the agterm terminal (libghostty, a workspace and session sidebar, Live sessions, the `agtermctl` control CLI). Each release is cut and published automatically, only after native Omarchy gates under Hyprland pass on both architectures, and carries a build-provenance attestation. If a release breaks, OPR doesn't take it for 24 hours (`min_release_age`), while the same gates rerun on the published release; a failure demotes it to a prerelease, which the upstream watch skips.

The app is umputun's [agterm](https://github.com/umputun/agterm) (MIT), and the GTK4/libadwaita Linux port is melonamin's [agterm-linux](https://github.com/melonamin/agterm-linux), whose releases ship x86_64 packages (Arch, .deb, .rpm). This package builds from releases of my fork, [agterm-omarchy](https://github.com/alexandru-savinov/agterm-omarchy). The fork adds aarch64 builds, the gated and attested releases described above, and two fixes: a launcher-scoped retry for GTK's EGL config request on Mesa 26.3.1 (Omarchy edge, [GNOME/gtk#8566](https://gitlab.gnome.org/GNOME/gtk/-/work_items/8566)), and HTML-overlay paste that holds a paste until the clipboard's formats are known and refuses mixed file and text offers.

**Package**

- Installs the [`omarchy-v0.43.0.13`](https://github.com/alexandru-savinov/agterm-omarchy/releases/tag/omarchy-v0.43.0.13) archives, with the sha256 values from its `SHA256SUMS`, to `/usr/lib/agterm`. It adds `/usr/bin/agterm-linux` and `/usr/bin/agtermctl` symlinks, the desktop entry, the icons and the license.
- Extracts the archive with `bsdtar --no-same-owner`. The Swift runtime, libghostty and a private zmx ship as CI built and tested them (`!strip !debug`), and the bundled terminfo and Ghostty resources stay under `/usr/lib/agterm`.
- `depends` are the owners of the binaries' `NEEDED` libraries outside the archive's own `lib/`.
- `provides=(agterm-linux)` and `conflicts=(agterm-linux agterm-linux-bin)`, because `agterm-linux-bin` (melonamin's release package, and the AUR package of the same name) installs the same commands. Neither name is in OPR, so there is no `approved` line.
- Upstream watch on `omarchy-v` GitHub Releases, with `min_release_age` set to `24h`.
- The `release/owners.json` line was written by `bin/release-owners agterm-omarchy-bin`.
- `agterm-omarchy-bin.install` prints how to turn on the Omarchy integration after install, and the absolute CLI path to use if an older `~/.local` agterm shadows it.

**On Omarchy**

<!-- Reviewer evidence. Strip every HTML comment from pr-body.md before posting.
     VM = ops native-feel/results/omarchy-v0.43.0.13-20261122-183941.tsv: aarch64 Omarchy VM, LIBGL_ALWAYS_SOFTWARE=1,
     agterm 0.43.0.13 (76d0a19d) packaged by omarchy-pkgs bin/build (c00632a) and installed with pacman -U,
     `integration omarchy enable` on. 65 PASS, 0 FAIL in column 4. -->

- `agtermctl integration omarchy enable` makes agterm the default terminal, adds Super+C/V copy and paste, Omarchy's terminal opacity and its touchpad scroll factor, and warns when an older `~/.local` desktop entry shadows the package. `disable` restores `hyprland.lua` and `xdg-terminals.list` byte for byte.
<!-- VM "SETUP integration omarchy enable" (terminalSelection firstEntry io.github.melonamin.agterm.desktop, state managed);
       "KEYS Super+C copies without interrupting" PASS; "KEYS Super+V pastes" PASS; "LOOK Opacity active/inactive" PASS (0.985/0.96);
       "LOOK Touchpad scroll factor rule" PASS (scroll_touchpad 0.2, Omarchy's Ghostty value);
       "REMOVAL Shadowed install: status names the ~/.local entry, enable warns, ~/.local unchanged" PASS;
       "REMOVAL integration omarchy disable restores hyprland.lua" PASS (hyprland.lua and xdg-terminals.list identical). -->
- Super+Return and Omarchy's launchers open agterm: Super+Return opens a new window in the running agterm, "new terminal in current directory" opens in the pane's folder, and `omarchy-launch-tui`, the floating launcher, the Install menu's Web App entry and the drop-down console's first command open with Omarchy's app ID.
<!-- VM "LAUNCHING Super+Return cold" PASS (3/3); "Super+Return warm" PASS (windows 1 -> 2); "New terminal in current directory" PASS;
       "Symlinked working directory" PASS; "omarchy-launch-tui btop" PASS (org.omarchy.btop); "omarchy-launch-floating-terminal-with-presentation" PASS;
       "Omarchy menu installer entry (Install > Web App)" PASS; "AROUND Drop-down console first open (Super+`) runs its command" PASS. -->
- With **Follow Omarchy**, on by default, agterm follows `omarchy theme set` and `omarchy font set` live, `omarchy display text size`, Omarchy's 14 pt padding and its non-blinking cursor; a btop window survives a theme change.
<!-- VM "LOOK omarchy theme set follows live" PASS (111C18 -> 1A1B26); "LOOK omarchy font set follows" PASS (1007 glyph pixels changed, no restart);
       "LOOK Text size (omarchy-display-text-size 20)" PASS (15 pt -> fontSize 15); "LOOK Padding vs Omarchy Ghostty (14x14)" PASS;
       "LOOK Cursor does not blink" PASS; "AROUND btop window survives omarchy theme set and SIGUSR2" PASS.
       "on by default": the run never toggled the setting; OmarchyTheme.swift `follows` falls back to `theme.isInstalled`. -->
- Shift+Enter and Alt+Shift+Enter send the same CSI-u bytes as Omarchy's Ghostty (`ESC[13;2u`, `ESC[13;4u`), so agents that bind them behave as they do in Ghostty; Shift+Insert pastes and Ctrl+Insert copies.
<!-- VM "KEYS Shift+Enter sends CSI-u" PASS, "KEYS Alt+Shift+Enter sends CSI-u" PASS (bytes equal Ghostty's), "KEYS Shift+Insert pastes" PASS,
       "KEYS Ctrl+Insert copies" PASS. -->
- Works with the community bar plugin [agterm for Omarchy](https://github.com/dmitriiweb/omarchy-agterm) by dmitriiweb: it shows agterm's sessions, and an agent's status hook updates its count.
<!-- VM "AROUND Bar plugin reads agterm (online, windows)" and "AGENTS Bar plugin count follows the hook" (FEATURE rows, observed PASS),
       plugin dmitriiweb.agterm pinned at dff31701. Name: its manifest.json; URL: ops vm-results.md line 46.
       Not claimed: focusing agterm from the bar right after a fresh agterm start was observed FAIL ("Window select raises agterm right after a fresh start"). -->
- agterm's quick terminal (Ctrl+\`) doesn't collide with Omarchy's console (Super+\`).
<!-- VM "AROUND Super+` goes to Omarchy's console, not agterm" PASS; "agterm quick terminal (Ctrl+`) in agterm" observed PASS. -->

**Known limitations**

- Omarchy's **Setup ▸ Defaults ▸ Terminal** menu has no agterm entry, so it shows no checkmark while agterm is the default.
<!-- VM "AROUND Default-terminal menu checkmark" LIMITATION (menu entries naming agterm: 0). -->
- In a window with several sessions, `omarchy-cmd-terminal-cwd` returns the folder of the newest shell, not the focused one.
<!-- VM "LAUNCHING Terminal cwd with two live shells (omarchy-cmd-terminal-cwd)" LIMITATION (focused /usr/share, returned /etc). -->
- agterm's quick terminal (Ctrl+\`) works inside an agterm window; there is no global hotkey under Hyprland.
<!-- VM "AROUND agterm quick terminal (Ctrl+`) in agterm" observed PASS; "... summoned from another workspace (Ctrl+`)" observed FAIL. -->
- Native feel was measured on aarch64 (an Omarchy VM) with software rendering only. Hardware GL rendering and touchpad scroll feel are untested.
<!-- VM "SETUP install" (LIBGL_ALWAYS_SOFTWARE=1); "LOOK Touchpad scroll feel" MANUAL (the VM has no working touchpad or wheel input). -->
- x86_64 is covered by the container install test below, not by the native-feel checks.
<!-- run 45047570475, job "pacman -U and Hyprland x86_64 (Arch container, vkms)". -->

**Tested**

- This repo's recipe build ran omarchy-pkgs' `bin/build` at `5c7d21d2` on `ubuntu-24.04` and `ubuntu-24.04-arm` ([run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/45047570475)):
  - every file is owned by root, and `pacman -U` prints the install note;
  - namcap reports no errors on the package or the PKGBUILD;
  - `ldd -r` resolves everything, the highest glibc symbol is `GLIBC_2.38`, and `desktop-file-validate` passes.
- `tests/pinned-sources.sh`, `tests/pkgname-unique.sh`, `bin/check-shadowed` and `bin/release-owners --check` pass, and `helpers/upstream-watch.py check` reports "already current".
- x86_64, in the native gates' Arch container with Omarchy's stable packages and Hyprland 0.56.2 on vkms (Mesa 26.2.5), from the package `bin/build` made in the same run:
  - `pacman -U` installed it without pulling in any new package;
  - through `/usr/bin`, the app passed 7/7 ordinary and 9/9 Live-session checks under Hyprland with software rendering;
  - `pacman -R` removed all 542 files and left no agterm or zmx process.
- aarch64, on an Omarchy VM, from a package `bin/build` made at omarchy-pkgs `c00632a`:
  - `pacman -U`, then the "On Omarchy" checks above, under Hyprland with software rendering;
  - `agtermctl integration omarchy disable` restored `hyprland.lua` and `xdg-terminals.list`, and `pacman -R` removed all 769 packaged paths and left the package set as it was.
<!-- VM "SETUP install" (769 paths), "REMOVAL pacman -R leaves no files" PASS (left behind: 0), "REMOVAL restored: pacman package set" PASS.
       The VM built from recipe 3516ed79 with pkgver/sha256 substituted (vm-tools/nf-run.sh line 101): the same recipe
       without install= and the .install file. The exact recipe bytes are covered by the CI run above on both architectures. -->
- The release's own native gates passed on x86_64 and aarch64 against Omarchy's stable channel before it was published, and again on the published release ([run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/45043865908)). The EGL retry passed the same gates on edge (Mesa 26.3.1) at an earlier commit with the same retry code ([run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/44647147715)).
<!-- run 45043865908 jobs native_candidate / Omarchy gates (x86_64, aarch64) success and watch_promotion / native_published success, CHANNEL: stable;
       promotion.json source_commit 76d0a19d. `git diff --stat 7b13b8df 76d0a19d -- linux/egl-compat.c LauncherChildEnvironment.swift` is empty. -->

![agterm-omarchy-bin 0.43.0.2 on Hyprland, aarch64 (an earlier version)](https://raw.githubusercontent.com/alexandru-savinov/agterm-omarchy/895bef327304aa8477724e57cb3ca61b28da0bcf/packaging/omarchy-pkgs/screenshots/agterm-omarchy-bin-aarch64-hyprland.png)

It's a draft so you can look before it merges itself; I'll mark it ready when you're happy with it.

Prepared and posted with AI assistance (Claude Code) on my behalf.

---

## Files and branch plan (for mac_lead, not posted)

The PR changes exactly four files on top of omarchy-pkgs `master`:

| File | Change | Source here |
|---|---|---|
| `pkgbuilds/agterm-omarchy-bin/PKGBUILD` | added | `packaging/omarchy-pkgs/agterm-omarchy-bin/PKGBUILD` |
| `pkgbuilds/agterm-omarchy-bin/agterm-omarchy-bin.install` | added | `packaging/omarchy-pkgs/agterm-omarchy-bin/agterm-omarchy-bin.install` |
| `pkgbuilds/agterm-omarchy-bin/.omarchy/package.json` | added | `packaging/omarchy-pkgs/agterm-omarchy-bin/.omarchy/package.json` |
| `release/owners.json` | one line added, between `aether` and `alsa-ucm-conf` | `packaging/omarchy-pkgs/owners.json.line` |

- **Fork and branch:**
  - the fork `alexandru-savinov/omarchy-pkgs` exists, with branch `add-agterm-omarchy-bin` at `2b7c0d23` on upstream `master` `2723852d`;
  - that commit is stale: it has the 0.43.0.2 recipe and no `.install`. Regenerate it with the commands below on current `master` before posting.
- **Base commits:** CI's checks ran at `5c7d21d2af2fa1bea38b5c0dffeda4dfe40a6ab6`. The branch is on `2723852d8b526e71c7cfc3c540d912138a1adb22` (2026-11-21), and nothing in `bin/`, `helpers/`, `tests/`, `build/`, `.github/` or the existing `release/owners.json` lines changed between them. At `2723852d`, on Arch, `bin/release-owners` reproduces the committed line, and `release-owners --check`, `package-metadata`, `package-owners`, `pgp-keys-vendored`, `pinned-sources` and `pkgname-unique` pass; `upstream-watch` reports "already current".
- **`release/owners.json`:** omarchy-pkgs' `AGENTS.md` says never to edit the directory lines by hand. Run `bin/release-owners agterm-omarchy-bin` on Arch, as a non-root user, and check that it adds exactly `owners.json.line`. This repository's `omarchy-pkgs recipe build` workflow does this on every run.

```bash
gh repo fork omacom/omarchy-pkgs --clone=false
git clone https://github.com/alexandru-savinov/omarchy-pkgs.git && cd omarchy-pkgs
git remote add upstream https://github.com/omacom/omarchy-pkgs.git && git fetch upstream
git switch -c add-agterm-omarchy-bin upstream/master
AGTERM=~/src/agterm-omarchy   # any checkout that has fetched claude/omarchy-pkg-465
mkdir -p pkgbuilds/agterm-omarchy-bin/.omarchy
for f in PKGBUILD agterm-omarchy-bin.install .omarchy/package.json; do
  git -C "$AGTERM" show 07825e4f:packaging/omarchy-pkgs/agterm-omarchy-bin/$f > pkgbuilds/agterm-omarchy-bin/$f
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

- **Recipe bytes:** `07825e4f` bumps `pkgver` to 0.43.0.13; the commands copy the three recipe files from it. The recipe build ran on #468 at `1f4fed8e`, whose recipe files are identical. The VM tested the same recipe without `install=` and the `.install` file (see the Tested comment).
- **PR body:** `pr-body.md` is everything between the two `---` rules above, with every `<!-- -->` comment removed:

  ```bash
  python3 -c 'import re,sys; s=open(sys.argv[1]).read().split("\n---\n")[1]; s=re.sub(r"\n<!--.*?-->(?=\n)", "", s, flags=re.S); print(re.sub(r"\n{3,}", "\n\n", s).strip())' \
    packaging/omarchy-pkgs/SUBMISSION-pr.md > pr-body.md
  ```
- **Build approval:** `alexandru-savinov` isn't in `.github/VOUCHED.td` or a collaborator, so the PR shows **Awaiting build approval** until a maintainer labels it `build-approved`.
- **Auto-merge:** marking the PR ready for review, once it's trusted and green, arms auto-merge and publishes to `edge`.
