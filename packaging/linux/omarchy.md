# Omarchy / Arch Linux

Build agterm natively on `x86_64` or `aarch64` using the GTK4 Linux frontend.
The architecture is selected from the machine running the build; an x86_64 release archive cannot run
in an ARM64 VM.

From a checkout of the `linux-port` branch:

```bash
scripts/setup-omarchy.sh
scripts/install-linux.sh
```

Run setup as the desktop user.
It uses `sudo pacman -Syu --needed` for system dependencies, preserving Arch's full-upgrade requirement;
review pacman's transaction before accepting it.
`--skip-packages` skips that transaction when dependencies are already provisioned.
`--with-ui-tests` additionally installs the Xvfb, Openbox, and accessibility test dependencies.

Setup installs Swift 6.3.2 from the official Ubuntu 24.04 toolchain through mise and ensures Zig 0.16.0
is available, then builds pinned libghostty and zmx with `scripts/setup-linux.sh`.
If Zig cannot negotiate a dependency server's TLS handshake, the build helper retries that archive with
curl's certificate verification; Zig still hashes it and the original manifest still selects the package.
Runtime source, compiler caches, and fallback downloads use `/var/tmp` by default because Omarchy's
`/tmp` is usually backed by RAM; an explicit `TMPDIR` overrides that choice.
Ghostty and zmx share a content-addressed dependency cache during setup.
The Swift platform is explicit because older mise releases construct a nonexistent `archarm` download URL.
Arch's `libxml2-legacy` supplies Swift's required `libxml2.so.2` ABI.
Only ncurses aliases live in `~/.local/share/agterm/swift-compat` (or under `XDG_DATA_HOME`);
the build helpers add that directory to their environment without changing shell startup files.

The personal installer places the complete runtime under `~/.local/lib/agterm`, commands under
`~/.local/bin`, and an **agterm** launcher in the desktop application menu.
Use `PREFIX=/another/prefix scripts/install-linux.sh` for a different destination.
The installed runtime includes Swift libraries, libghostty, zmx, themes, shell integration, terminfo,
and the agent integration resources; the source checkout is not needed to run it.

Launch **agterm** from Omarchy's application launcher, or run:

```bash
~/.local/bin/agterm-linux
```

GTK selects native Wayland in a Hyprland session.
No Hyprland configuration or default terminal selection is changed by installation.
For an optional dedicated shortcut, add this to `~/.config/hypr/bindings.conf` and reload Hyprland:

```ini
bind = SUPER SHIFT, Return, exec, ~/.local/bin/agterm-linux
```

Choose a chord that is unused in your configuration.
See the [Wayland support matrix](../../agterm-linux/docs/x11-wayland.md) for compositor-owned behavior
such as window placement and blur.

## Verification

The build helpers select Swift and its library environment; use the same environment for direct tests:

```bash
source linux/swift-env.sh
"${SWIFT_COMMAND[@]}" test --package-path agtermCore --no-parallel
"${SWIFT_COMMAND[@]}" test --package-path agterm-linux --no-parallel
scripts/test-linux-ui.sh
scripts/test-omarchy.sh
```

The accessibility suite uses an isolated X11 display and state directory.
`test-omarchy.sh` uses the current Hyprland desktop with separate app state and socket, and writes its
control transcript and screenshots under `artifacts/`.
Run it from a terminal in that desktop so the Wayland and Hyprland environment is available.
For a development launch, isolate its state and control socket from an installed instance:

```bash
AGTERM_STATE_DIR="$(mktemp -d /tmp/agterm-check.XXXXXX)" scripts/run-linux.sh
```

Point control commands at that instance's `agterm.sock` with `--socket` after the subcommand.
The CLI does not automatically consume `AGTERM_SOCKET`.
