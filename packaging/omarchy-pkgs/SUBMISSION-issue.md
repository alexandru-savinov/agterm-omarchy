# Package request issue for omacom/omarchy-pkgs

omarchy-pkgs has no issue template or CONTRIBUTING file; this follows its open `Package request:` issues.
Paste the title and everything below the rule. Nothing has been posted.

**Title:** `Package request: agterm-omarchy-bin (agterm terminal, x86_64 and aarch64)`

---

## Request

I'd like to add `agterm-omarchy-bin` to OPR as an optional package, and I'm asking before I open the PR.

agterm is a native terminal built on libghostty, with a sidebar of workspaces and sessions, Live sessions that survive an app restart, and a control CLI (`agtermctl`). The app is umputun's [agterm](https://github.com/umputun/agterm) (MIT). The GTK4/libadwaita Linux port is melonamin's [agterm-linux](https://github.com/melonamin/agterm-linux), which ships x86_64 only.

The package repackages releases of my fork, [agterm-omarchy](https://github.com/alexandru-savinov/agterm-omarchy), which adds:

- aarch64 builds next to x86_64;
- a launcher-scoped retry for GTK's EGL config request on Mesa 26.3.1 (Omarchy edge), where GTK 4.24 asks for ES1-capable configs that Mesa no longer advertises and the app fails with `No EGL configuration available` ([GNOME/gtk#8566](https://gitlab.gnome.org/GNOME/gtk/-/work_items/8566)).

## Releases

- A release is cut automatically from a green `main` push. CI builds both architectures natively, then native gates install the archive and run it under Hyprland on x86_64 and aarch64: an Arch container with Omarchy's packages, vkms, and Mesa software rendering. Each archive has a GitHub build-provenance attestation.
- Tags are `omarchy-v<upstream version>.<serial>`; the first is [`omarchy-v0.43.0.1`](https://github.com/alexandru-savinov/agterm-omarchy/releases/tag/omarchy-v0.43.0.1), from agterm v0.43.0.
- Each release is gated again for 24 hours after publishing, and a failure marks it a prerelease. The recipe's upstream watch skips prereleases and sets `min_release_age` to `24h`. There is no fixed cadence: each release is an update of about 78 MB per architecture.

## Package

- `arch=(x86_64 aarch64)`, `source: local`, an upstream watch on `omarchy-v` releases.
- `provides=(agterm-linux)` and `conflicts=(agterm-linux agterm-linux-bin)`, because AUR `agterm-linux-bin` installs the same commands. Neither name is in OPR, so no `approved` line is needed.
- Built and checked with `bin/build` on both architectures, then installed, run under Hyprland, and removed on an aarch64 Omarchy VM: [test evidence](https://github.com/alexandru-savinov/agterm-omarchy/pull/468#issuecomment-7056865377).

Would you take it? If so, I'll open a draft PR with the recipe and its `release/owners.json` line.

Prepared with AI assistance ([Claude Code](https://claude.com/claude-code)) and reviewed by me before posting.
