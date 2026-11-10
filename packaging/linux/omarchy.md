# Omarchy / Arch Linux

The build scripts select `x86_64` or `aarch64` from the machine running the GTK4 frontend build.
An x86_64 archive cannot run in an ARM64 VM.
[Native Omarchy evidence](https://github.com/alexandru-savinov/agterm-omarchy/tree/48e70b33651ca8855f8cef5bd57f50afa67ed291/docs/evidence/omarchy-6775c336-2026-11-01) is for the exact ARM64 payload
and recorded conditions; x86_64 has hosted CI evidence, with native Omarchy acceptance still unverified.
That record binds source `6775c336`, run `41357189891` and ARM64 archive SHA256 prefix `2b629e40`.
The [earlier fd85fc58 acceptance](https://github.com/alexandru-savinov/agterm-omarchy/tree/4d05d60c928c52b04df2ba51c1861cc1b90b8326/docs/evidence/omarchy-fd85fc58-2026-11-01) remains separate.
A fresh source build needs its own validation.

## Install from source

Clone this collaboration repository's integrated `main` branch:

```bash
git clone --branch main https://github.com/alexandru-savinov/agterm-omarchy.git
cd agterm-omarchy
scripts/setup-omarchy.sh
mise exec swift@6.3.2 -- scripts/install-linux.sh
```

`omarchy` is the coordinated candidate branch; use the task's exact branch/SHA when reproducing a
candidate, and keep that identity with the resulting archive and test records.
The inherited upstream `master` / `linux-port` names are not this fork's normal clone route.
For download availability, check [this fork's releases](https://github.com/alexandru-savinov/agterm-omarchy/releases)
and [publication task #17](https://github.com/alexandru-savinov/agterm-omarchy/issues/17).
The source Linux fork's DEB/RPM releases are separate builds, not Omarchy artifacts from this checkout.

Run setup as the desktop user.
It uses `sudo pacman -Syu --needed` for system dependencies, preserving Arch's full-upgrade requirement;
review pacman's transaction before accepting it.
`--skip-packages` skips that transaction when dependencies are already provisioned.
Use `scripts/setup-omarchy.sh --with-ui-tests` during setup if you plan to run the test commands below.
It adds Xvfb, xauth, Openbox, xdotool, Python GObject/AT-SPI and `grim`.
Python and ImageMagick (`magick`) are in the base dependency list; the Hyprland test desktop must supply `hyprctl`.

Setup installs Swift 6.3.2 from the official Ubuntu 24.04 toolchain through mise and ensures Zig 0.16.0
is available, then builds pinned libghostty and zmx with `scripts/setup-linux.sh`.
If Zig reports a dependency TLS handshake failure or an HTTP connection reset, the build helper retries
that archive with curl's certificate verification; Zig still hashes it and the original manifest still
selects the package.
Runtime source, compiler caches, and fallback downloads use `/var/tmp` by default because Omarchy's
`/tmp` is usually backed by RAM; an explicit `TMPDIR` overrides that choice.
Ghostty and zmx share a content-addressed dependency cache during setup.
The zmx helper uses an explicit `x86_64-linux-gnu.2.39` or `aarch64-linux-gnu.2.39` target
and Zig's `baseline` CPU, so release binaries do not require the build runner's CPU extensions.
Its cache records that target and CPU in `BUILD_POLICY` alongside the pinned source/patch `REVISION`.
Setup rejects older caches without that policy, mismatched policies, and wrong-architecture helpers
before executing them, then rebuilds zmx while retaining any independently verified Ghostty cache.
The Swift platform is explicit because older mise releases construct a nonexistent `archarm` download URL.
Arch's `libxml2-legacy` supplies Swift's required `libxml2.so.2` ABI.
Only ncurses aliases live in `~/.local/share/agterm/swift-compat` (or under `XDG_DATA_HOME`);
the build helpers add that directory to their environment without changing shell startup files.
Build helpers honor an explicit `SWIFT` executable first, then `swift` on `PATH`, then mise.
The install example selects Swift 6.3.2 through mise; if `SWIFT` is already set, ensure it also names that version.

The personal installer places the complete runtime under `~/.local/lib/agterm`, commands under
`~/.local/bin`, and an **agterm** launcher in the desktop application menu.
The installer builds Release; `--no-build` uses the checkout's existing `.build/release` products,
and `--payload DIR` installs an extracted archive directory (such as `agterm-stage/`) without building.
Use `--prefix /another/prefix` (or `PREFIX=/another/prefix`) on the install command for another destination;
the installer warns when that prefix's `share/` is not on the XDG data path, where launchers look.
`--uninstall` removes exactly the files the installer recorded and keeps state in `~/.local/share/agterm`
and configuration in `~/.config/agterm`; add `--purge-state` to delete the state directory too,
including setup's `swift-compat`.
For a VM with the documented EGL failure, add `--software-rendering` when installing; see [Rendering](#rendering).
The installed runtime includes Swift libraries, libghostty, zmx, themes, shell integration, terminfo,
and the agent integration resources; the source checkout is not needed to run it.

Launch **agterm** from Omarchy's application launcher, or run:

```bash
~/.local/bin/agterm-linux
```

GTK selects native Wayland in a Hyprland session.
No Hyprland configuration or default terminal selection is changed by installation.
For an optional dedicated shortcut on Lua-based Hyprland (0.55 and later), add this to the user
bindings file loaded by your configuration and reload Hyprland:

```lua
hl.bind("SUPER + SHIFT + Return", hl.dsp.exec_cmd(os.getenv("HOME") .. "/.local/bin/agterm-linux"))
```

Older Hyprland configurations use the following in `~/.config/hypr/bindings.conf`:

```ini
bind = SUPER SHIFT, Return, exec, ~/.local/bin/agterm-linux
```

Choose a chord that is unused in your configuration.
See the [Wayland support matrix](../../agterm-linux/docs/x11-wayland.md) for compositor-owned behavior
such as window placement and blur.

## Rendering

If an accelerated UTM guest reports `No EGL configuration available`, use Mesa software rendering for
one app launch:

```bash
LIBGL_ALWAYS_SOFTWARE=1 "$HOME/.local/bin/agterm-linux"
```

To retain software rendering for command-line and desktop-menu launches across reinstalls:

```bash
mise exec swift@6.3.2 -- scripts/install-linux.sh --software-rendering
# Return to the normal inherited renderer environment:
mise exec swift@6.3.2 -- scripts/install-linux.sh --default-rendering
```

These commands reinstall from this checkout; add `--no-build` to use its existing Release build.
Without either renderer option, reinstalling preserves the prior selection; new installations use the
normal inherited environment.
An invalid stored selection requires one of the explicit renderer flags.
The installer stores `software` or `default` as data in the payload's `share/agterm/RENDERER` file.
Only the app wrapper reads this file; the `agtermctl` helper and compositor/global settings are unchanged.
Software mode overrides an inherited `LIBGL_ALWAYS_SOFTWARE=0` for the app launch; default mode leaves
the caller's value untouched, including an explicit one-shot override.
Child processes launched by the app can inherit software rendering as well.
The opt-in addresses the tested VM's EGL limitation; it is not enabled automatically for other Linux machines.
Hardware rendering remains dependent on each machine's Mesa/driver configuration.

## Verification

Provision the test tools with `--with-ui-tests` as described above.
From the repository root, select the same Swift and compatibility-library environment as the build helpers:

```bash
source linux/swift-env.sh
"${SWIFT_COMMAND[@]}" --version  # Swift 6.3.2 is required.
"${SWIFT_COMMAND[@]}" test --package-path agtermCore --no-parallel
"${SWIFT_COMMAND[@]}" test --package-path agterm-linux --no-parallel
"${SWIFT_COMMAND[@]}" build --package-path agterm-linux
scripts/test-linux-ui.sh
```

The explicit build creates the Debug app and CLI used by the accessibility runner's defaults.
The runner checks `dbus-run-session`, `dbus-send`, `openbox`, `xdotool`, `xvfb-run`, Python and the
GObject/AT-SPI bindings, and uses a private HOME, runtime directory, state, socket, D-Bus session and X11 display.
It does not establish native Wayland acceptance.

### Native Wayland test environment

Run native acceptance on a dedicated Hyprland test desktop without another agterm instance using the
same runtime directory, or through a reviewed operator that isolates the frontend's generated runtime files.
The known shared generated-config path collision is tracked in [#124](https://github.com/alexandru-savinov/agterm-omarchy/issues/124)
and the proposed application fix in [#126](https://github.com/alexandru-savinov/agterm-omarchy/issues/126).
`AGTERM_STATE_DIR` alone does not isolate those Ghostty files.
The raw `test-omarchy.sh` creates separate HOME/config/state/socket paths but retains the desktop's
`XDG_RUNTIME_DIR` and session bus, so it needs the same dedicated-environment or reviewed-operator condition.
Simply changing `XDG_RUNTIME_DIR` can also break Wayland/compositor access; the recorded native acceptance
used an external adapter with a private frontend runtime and an explicit route to the existing Wayland socket.
That adapter did not fix the application source.

The native harness needs `hyprctl`, `grim`, `magick` and Python, plus `WAYLAND_DISPLAY` and
`HYPRLAND_INSTANCE_SIGNATURE` from a terminal inside that test desktop.
It focuses windows and sends test input; run it while the test desktop is available for that purpose.
It supports both legacy and Lua-based Hyprland dispatch commands.

To test the installed Release payload in that environment, select its app and CLI wrappers explicitly:

```bash
AGTERM_TEST_BIN="$HOME/.local/bin/agterm-linux" \
AGTERM_TEST_CTL="$HOME/.local/bin/agtermctl" \
scripts/test-omarchy.sh
```

For a custom installation prefix, replace both paths with that prefix's `bin` paths.
The wrappers select the installed payload's libraries, resources and bundled zmx.
Without these overrides the harness tests `agterm-linux/.build/debug/AgtermLinux` and
`agterm-linux/.build/debug/agtermctl-linux`; installing Release alone does not create those Debug products.
On software-rendered VMs, the installed wrapper's saved renderer selection applies, or prefix the test
command with `LIBGL_ALWAYS_SOFTWARE=1` for a one-shot override.
Control transcripts, screenshots and result/cleanup identities go under `artifacts/omarchy-<timestamp>`;
`AGTERM_OMARCHY_ARTIFACTS` may name another empty output directory.
Record the tested source, archive and harness identities with the results.

### Live mode

Live sessions retain upstream's requirement that the account's login shell is **zsh**, as reported by
its password database.
Installing zsh or setting `SHELL` alone does not select it; the installer does not change the account's login shell.
On Omarchy's default bash account, fresh shells and command restore work, while Live mode reports its
zsh prerequisite and falls back to fresh shells.
The recorded native acceptance used a process-local zsh fixture while preserving the actual bash account;
that result does not establish default-bash Live support.

For a test account already configured with zsh, use the same dedicated environment and installed paths:

```bash
AGTERM_TEST_BIN="$HOME/.local/bin/agterm-linux" \
AGTERM_TEST_CTL="$HOME/.local/bin/agtermctl" \
AGTERM_TEST_RESTORE_MODE=live scripts/test-omarchy.sh
```

This variant checks shell PID continuity across a frontend restart and test-backend cleanup.
A development launch through `scripts/run-linux.sh` builds Debug by default and has the same generated-runtime
isolation requirement when another instance exists.
For manual control commands, pass `--socket` after the subcommand to select the intended instance;
the CLI does not automatically consume `AGTERM_SOCKET`.

### Distribution notices

Staging retains the repository MIT license and pinned Ghostty/Swift component notices under
`share/licenses/agterm/`, including immutable source URLs, notice hashes and copied-library hashes.
Missing or stale Ghostty cache notices are rejected before reuse.
See [the notice profile and remaining distribution review](notices/README.md) before publication;
these component copies do not establish complete transitive attribution or qualify a new archive for release.
