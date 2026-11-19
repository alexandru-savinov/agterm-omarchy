#!/usr/bin/env bash
# Run the native Omarchy gates (ordinary, Live, config probe x10, uninstall) against one architecture's
# archive inside an Arch Linux (x86_64) or Arch Linux ARM (aarch64) container on a same-architecture host
# whose vkms module supplies a KMS device.
# Called by .github/workflows/omarchy-x86-native.yml as root in the container; drops to an unprivileged
# bash-login user for everything except package installation.
# Mounts: /repo (read-only checkout), /release (verified archive + SHA256SUMS), /out.
# GATE_ARCHIVE names the archive in /release.
#
# Threat model: these gates catch defects in archives that main CI built, not malice in them. The bytes'
# integrity comes from the attestation and resolve-once byte binding checked on the host before this runs.
# The payload shares a UID with this harness and could rewrite gates.tsv, but a payload able to do that
# means main is compromised, and main also controls these scripts, so a separate UID would not change who
# is trusted. A separate UID and a read-only install remain optional hardening.
set -euo pipefail
PHASE="${1:-root}"
SELF="$(readlink -f "$0")"
USER_NAME=omarchy
PREFIX_NAME=agt-prefix
ARCHIVE="${GATE_ARCHIVE:?GATE_ARCHIVE names the archive in /release}"
PROBE_RUNS="${PROBE_RUNS:-10}"
SETUP_DEADLINE=15m
SESSION_DEADLINE=50m
OMARCHY_CHANNEL="${OMARCHY_CHANNEL:-stable}"

# Omarchy's desktop-runtime packages that are in the official Arch repositories, from
# basecamp/omarchy install/omarchy-base.packages at OMARCHY_REF. Omarchy-repo/AUR packages (walker, aether,
# omarchy-nvim, ...) and unrelated applications (office, media, browsers, docker) are left out.
OMARCHY_REF=3b36b9e19ea00a6c21492098308a7f44a8091564
OMARCHY_PACKAGES=(hyprland hyprland-guiutils hypridle hyprlock hyprpicker hyprsunset xdg-desktop-portal-hyprland
  xdg-desktop-portal-gtk uwsm mako waybar swaybg swayosd wl-clipboard grim slurp imagemagick fcitx5 fcitx5-gtk
  gnome-keyring gnome-themes-extra yaru-icon-theme noto-fonts noto-fonts-emoji ttf-jetbrains-mono-nerd
  fontconfig polkit-gnome xdg-terminal-exec bash-completion python-gobject jq libsecret)
# Omarchy's own Arch (and Arch Linux ARM) snapshot mirrors, from default/pacman/mirrorlist-<channel> and
# default/pacman/aarch64/mirrorlist-<channel> at OMARCHY_REF. stable is what an Omarchy install uses on x86
# and generic aarch64 (install/helpers/pacman.sh); it lags rolling Arch.
omarchy_mirror() {
  # shellcheck disable=SC2016 # pacman expands $repo and $arch.
  case "$(uname -m)-$OMARCHY_CHANNEL" in
    x86_64-stable) echo 'https://stable-mirror.omarchy.org/$repo/os/$arch' ;;
    x86_64-rc) echo 'https://rc-mirror.omarchy.org/$repo/os/$arch' ;;
    x86_64-edge) echo 'https://mirror.omarchy.org/$repo/os/$arch' ;;
    aarch64-stable) echo 'https://arm-stable-mirror.omarchy.org/$arch/$repo' ;;
    aarch64-rc) echo 'https://arm-rc-mirror.omarchy.org/$arch/$repo' ;;
    aarch64-edge) echo 'https://arm-mirror.omarchy.org/$arch/$repo' ;;
    *) echo "no Omarchy mirror for $(uname -m) channel $OMARCHY_CHANNEL" >&2; return 1 ;;
  esac
}

# The archive's runtime: GTK/WebKitGTK, Mesa software GL, zsh for the Live fixture, a compiler for it.
RUNTIME_PACKAGES=(gtk4 libadwaita webkitgtk-6.0 libepoxy mesa libglvnd zsh python dbus git gcc
  desktop-file-utils procps-ng file mesa-utils)

# Mirror fetches only; package signatures are still verified on every attempt.
pacman_retry() {
  local attempt
  for attempt in 1 2 3; do
    pacman "$@" && return
    echo "pacman $* failed (attempt $attempt)" >&2
    sleep $((attempt * 15))
  done
  return 1
}

# The Arch Linux ARM rootfs is a bootable system, not a container image: it ships no pacman keyring, a
# kernel and firmware whose upgrade would rebuild an initramfs, and a stock user holding uid 1000.
prepare_base() {
  [[ -d /etc/pacman.d/gnupg ]] || { pacman-key --init && pacman-key --populate; } > /out/pacman-key.log 2>&1
  local boot=()
  mapfile -t boot < <(pacman -Qq | grep -E '^(linux-aarch64|linux-firmware.*)$' || true)
  (( ${#boot[@]} == 0 )) || pacman -Rdd --noconfirm "${boot[@]}" > /out/base-removed.txt
  if getent passwd alarm >/dev/null; then userdel --remove alarm; fi
  if getent passwd 1000; then echo "uid 1000 is taken" >&2; return 1; fi
  # pacman's download sandbox stays on unless its Landlock ruleset is refused, as it is in the Arch Linux
  # ARM container on ubuntu-24.04-arm; then only the filesystem part is dropped, and signatures still apply.
  if ! pacman -Sy > /out/pacman-sync.log 2>&1 && grep -q 'Landlock ruleset could not be applied' /out/pacman-sync.log; then
    sed -i '/^\[options\]/a DisableSandboxFilesystem' /etc/pacman.conf
    echo "DisableSandboxFilesystem: pacman's Landlock ruleset was refused" > /out/pacman-sandbox.txt
  fi
}

# Package setup: the Omarchy channel mirror, the base's preparation, Omarchy's packages and the runtime.
setup_phase() {
  grep -E '^(ID|NAME|BUILD_ID)=' /etc/os-release > /out/base-os.txt
  local mirror
  mirror="$(omarchy_mirror)"
  printf 'Server = %s\n' "$mirror" > /etc/pacman.d/mirrorlist
  prepare_base
  # -uu: the snapshot can be older than the base image's own packages.
  pacman_retry -Syuu --noconfirm
  local available=() package
  for package in "${OMARCHY_PACKAGES[@]}"; do
    if pacman -Si "$package" >/dev/null 2>&1; then available+=("$package"); else echo "$package" >> /out/omarchy-packages-unavailable.txt; fi
  done
  pacman_retry -S --noconfirm --needed "${available[@]}" "${RUNTIME_PACKAGES[@]}"
  pacman -Q > /out/packages.txt
  { echo "omarchy package list: basecamp/omarchy@$OMARCHY_REF install/omarchy-base.packages"
    echo "omarchy channel: $OMARCHY_CHANNEL, Server = $mirror"; } > /out/omarchy-ref.txt
}

finish_root() {
  chown -R "$1" /out
  chmod -R u+rwX,go+rX /out
  exit "$2"
}

root_phase() {
  local owner status=0
  owner="$(stat -c %u:%g /out)"
  # Setup and session each have a deadline, so the job always has time left to stage and upload evidence.
  timeout --kill-after=30s "$SETUP_DEADLINE" bash "$SELF" setup || status=$?
  if (( status != 0 )); then
    printf 'setup\tFAIL exit=%s deadline=%s\n' "$status" "$SETUP_DEADLINE" >> /out/gates.tsv
    finish_root "$owner" "$status"
  fi
  useradd --create-home --uid 1000 --shell /bin/bash "$USER_NAME"
  # The container's own /dev nodes; the host's permissions are unchanged.
  chown "$USER_NAME" /dev/dri/card* /dev/dri/renderD* 2>/dev/null || true
  install -d -m 0700 -o "$USER_NAME" /run/user/1000
  install -d -m 0700 -o "$USER_NAME" "/home/$USER_NAME/evidence"
  timeout --kill-after=60s "$SESSION_DEADLINE" runuser -u "$USER_NAME" -- env -i PATH=/usr/local/bin:/usr/bin HOME="/home/$USER_NAME" USER="$USER_NAME" \
    LOGNAME="$USER_NAME" SHELL=/bin/bash LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/1000 PROBE_RUNS="$PROBE_RUNS" \
    OMARCHY_CHANNEL="$OMARCHY_CHANNEL" \
    GATE_ARCHIVE="$ARCHIVE" \
    dbus-run-session -- bash "$SELF" session || status=$?
  (( status != 124 && status != 137 )) ||
    printf 'session\tFAIL deadline %s reached\n' "$SESSION_DEADLINE" >> "/home/$USER_NAME/evidence/gates.tsv"
  # Copying "evidence/." would also copy its 0700 mode and owner onto /out itself.
  cp -a "/home/$USER_NAME/evidence/." /out/
  finish_root "$owner" "$status"
}

drm_card() {
  local card
  for card in /sys/class/drm/card[0-9]; do
    # vkms registers a bare platform device named vkms, with no driver link.
    if [[ "$(basename "$(readlink -f "$card/device")")" == vkms ]]; then
      echo "/dev/dri/$(basename "$card")"
      return
    fi
  done
  echo "no vkms card found" >&2
  return 1
}

start_hyprland() {
  local config="$HOME/.config/hypr/hyprland.lua"
  mkdir -p "$(dirname "$config")"
  # Omarchy's default/hypr/envs.lua and the software-rendering (no-animations) toggle, without its theme,
  # bindings or autostart, which need the Omarchy tree. uwsm needs a systemd user session, so Hyprland
  # starts directly.
  cat > "$config" <<'LUA'
hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("GDK_BACKEND", "wayland,x11,*")
hl.env("QT_QPA_PLATFORM", "wayland;xcb")
hl.env("XDG_SESSION_TYPE", "wayland")
hl.env("XDG_CURRENT_DESKTOP", "Hyprland")
hl.env("XDG_SESSION_DESKTOP", "Hyprland")
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })
hl.config({
  xwayland = { force_zero_scaling = true },
  ecosystem = { no_update_news = true, no_donation_nag = true },
  misc = { disable_hyprland_logo = true, disable_splash_rendering = true },
  animations = { enabled = false },
  decoration = { blur = { enabled = false }, shadow = { enabled = false } },
})
LUA
  local card
  card="$(drm_card)"
  LIBSEAT_BACKEND=noop AQ_DRM_DEVICES="$card" HYPRLAND_NO_SD_NOTIFY=1 HYPRLAND_NO_SD_VARS=1 \
    Hyprland --config "$config" > "$EVIDENCE/hyprland.log" 2>&1 &
  HYPRLAND_PID=$!
  local deadline=$((SECONDS + 60)) socket=""
  while (( SECONDS < deadline )); do
    kill -0 "$HYPRLAND_PID" 2>/dev/null || { echo "Hyprland exited"; tail -n 80 "$EVIDENCE/hyprland.log"; return 1; }
    socket="$(find "$XDG_RUNTIME_DIR/hypr" -maxdepth 2 -name .socket.sock 2>/dev/null | head -n1 || true)"
    if [[ -n "$socket" ]] && compgen -G "$XDG_RUNTIME_DIR/wayland-*" >/dev/null; then break; fi
    sleep 0.5
  done
  [[ -n "$socket" ]] || { echo "Hyprland socket never appeared"; tail -n 80 "$EVIDENCE/hyprland.log"; return 1; }
  HYPRLAND_INSTANCE_SIGNATURE="$(basename "$(dirname "$socket")")"
  WAYLAND_DISPLAY="$(basename "$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -name 'wayland-*' ! -name '*.lock' | head -n1)")"
  export HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY
  deadline=$((SECONDS + 30))
  until hyprctl monitors -j | python3 -I -c 'import json,sys; sys.exit(0 if json.load(sys.stdin) else 1)'; do
    (( SECONDS < deadline )) || { echo "Hyprland has no monitor"; return 1; }
    sleep 0.5
  done
  { hyprctl version; hyprctl monitors; } > "$EVIDENCE/hyprland-info.txt"
  # vkms has no GPU, so GBM/EGL on it is Mesa's software rasterizer; record what Mesa reports.
  eglinfo -B > "$EVIDENCE/eglinfo.txt" 2>&1 || true
  eglinfo -p wayland > "$EVIDENCE/eglinfo-wayland.txt" 2>&1 || true
  egl_configs > "$EVIDENCE/egl-choose-config.txt" 2>&1 || true
  grep -iE "GL_RENDERER|renderer|llvmpipe|softpipe" "$EVIDENCE/hyprland.log" | head -n 20 >> "$EVIDENCE/hyprland-info.txt" || true
}

# GTK 4.24 chooses its EGL config without EGL_RENDERABLE_TYPE, which defaults to ES 1.x. Count what Mesa
# offers on the compositor's display for that request and for explicit GL and ES 3 requests.
egl_configs() {
  cat > "$HOME/egl-configs.c" <<'C'
#include <stdio.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <wayland-client.h>
int main(void) {
  struct wl_display *wayland = wl_display_connect(NULL);
  if (!wayland) return 1;
  EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_WAYLAND_KHR, wayland, NULL);
  EGLint major, minor;
  if (!eglInitialize(display, &major, &minor)) return 2;
  printf("EGL %d.%d %s\n", major, minor, eglQueryString(display, EGL_VENDOR));
  struct { const char *name; EGLint attributes[16]; } requests[] = {
    {"gtk-rgba8-window", {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_COLOR_BUFFER_TYPE, EGL_RGB_BUFFER, EGL_RED_SIZE, 8,
                          EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE}},
    {"opengl-window", {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE}},
    {"gles3-window", {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_NONE}},
    {"any", {EGL_NONE}},
  };
  for (unsigned i = 0; i < sizeof requests / sizeof requests[0]; i++) {
    EGLint count = 0;
    eglChooseConfig(display, requests[i].attributes, NULL, 0, &count);
    printf("%s\t%d\n", requests[i].name, count);
  }
  return 0;
}
C
  cc -o "$HOME/egl-configs" "$HOME/egl-configs.c" -lEGL -lwayland-client && "$HOME/egl-configs"
}

# TERM, then KILL after 30 s: a compositor stuck in teardown must not hold the session to its deadline.
stop_hyprland() {
  local tick
  kill "$HYPRLAND_PID" 2>/dev/null || return 0
  for tick in $(seq 60); do
    kill -0 "$HYPRLAND_PID" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$HYPRLAND_PID" 2>/dev/null; then
    echo "Hyprland ignored TERM for 30 s (tick $tick); sending KILL" >> "$EVIDENCE/hyprland-info.txt"
    kill -KILL "$HYPRLAND_PID" 2>/dev/null || true
  fi
  wait "$HYPRLAND_PID" 2>/dev/null || true
}

record() { printf '%s\t%s\n' "$1" "$2" >> "$EVIDENCE/gates.tsv"; }

harness_gate() {
  local name="$1" mode="$2" expected="$3"; shift 3
  local status=0
  env "$@" AGTERM_TEST_BIN="$PREFIX/bin/agterm-linux" AGTERM_TEST_CTL="$PREFIX/bin/agtermctl" \
    AGTERM_TEST_RESTORE_MODE="$mode" AGTERM_OMARCHY_ARTIFACTS="$EVIDENCE/$name" \
    /repo/scripts/test-omarchy.sh > "$EVIDENCE/$name.log" 2>&1 || status=$?
  python3 -I - "$EVIDENCE/$name" "$expected" "$status" "$name" >> "$EVIDENCE/gates.tsv" <<'PY'
import json, pathlib, sys
root, expected, status, name = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
try:
    result = json.loads((root / 'result.json').read_text())
    cleanup = json.loads((root / 'cleanup.json').read_text())
except (OSError, ValueError) as error:
    print(f'{name}\tFAIL exit={status} no result ({type(error).__name__})')
    raise SystemExit
checks = result.get('checks') or result.get('completed_checks') or []
ok = status == 0 and result.get('passed') is True and len(checks) == expected and cleanup.get('passed') is True
print(f"{name}\t{'PASS' if ok else 'FAIL'} {len(checks)}/{expected} exit={status} "
      f"cleanup={cleanup.get('passed')} daemons={cleanup.get('tracked_daemon_count')} "
      f"harness_matches_commit={result['identity']['harness'].get('matches_commit')}")
PY
}

session_phase() {
  EVIDENCE="$HOME/evidence"
  PREFIX="$HOME/$PREFIX_NAME"
  git config --global --add safe.directory /repo
  start_hyprland

  (cd /release && sha256sum --check --strict --ignore-missing SHA256SUMS) > "$EVIDENCE/sha256-check.txt"
  grep -q "^$ARCHIVE: OK$" "$EVIDENCE/sha256-check.txt"
  record archive "$(sha256sum "/release/$ARCHIVE" | cut -d' ' -f1)"
  local stage="$HOME/stage"
  mkdir -p "$stage"
  tar -xzf "/release/$ARCHIVE" -C "$stage"
  record payload_commit "$(cat "$stage/agterm-stage/share/agterm/COMMIT")"
  local elf
  elf="$(file -b "$stage/agterm-stage/bin/agterm-linux.bin" | cut -d, -f1-2)"
  record payload_elf "$elf"
  record host_arch "$(uname -m)"
  case "$(uname -m)" in
    x86_64) [[ "$elf" == *x86-64 ]] ;;
    aarch64) [[ "$elf" == *aarch64 ]] ;;
    *) false ;;
  esac || { echo "payload ELF ($elf) does not match host $(uname -m)" >&2; return 1; }
  record payload_version "$(cat "$stage/agterm-stage/share/agterm/VERSION")"
  /repo/scripts/install-linux.sh --payload "$stage/agterm-stage" --software-rendering --prefix "$PREFIX" \
    > "$EVIDENCE/install.log" 2>&1
  # Nothing launches unless the installed payload is the archive plus the installer's own files.
  local compared=0 installed
  installed="$(readlink -e "$PREFIX/lib/agterm")" && [[ -d "$installed" && ! -L "$installed" ]] || {
    record installed_vs_stage "FAIL no installed payload directory at $PREFIX/lib/agterm"
    return 1
  }
  python3 -I /repo/.github/omarchy-x86-native/compare-install.py "$stage/agterm-stage" "$installed" \
    > "$EVIDENCE/installed-vs-stage.txt" 2>&1 || compared=$?
  if (( compared != 0 )); then
    record installed_vs_stage "FAIL exit=$compared $(wc -l < "$EVIDENCE/installed-vs-stage.txt") differences"
    echo "the installed payload is not the archive; not launching it" >&2
    return 1
  fi
  record installed_vs_stage "PASS 0 differences"
  local app_sha
  app_sha="$(sha256sum "$PREFIX/lib/agterm/bin/agterm-linux.bin" | cut -d' ' -f1)"
  record app_sha256 "$app_sha"
  record renderer "$(cat "$PREFIX/lib/agterm/share/agterm/RENDERER")"
  for file in scripts/test-omarchy.sh scripts/omarchy-config-probe.py agterm-linux/tests/login_shell_zsh_fixture.c; do
    record "sha256:$file" "$(sha256sum "/repo/$file" | cut -d' ' -f1)"
  done
  record harness_blob "$(git -C /repo rev-parse HEAD:scripts/test-omarchy.sh)"
  record omarchy_channel "$OMARCHY_CHANNEL $(omarchy_mirror)"
  record mesa "$(pacman -Q mesa | cut -d' ' -f2)"
  record egl_gtk_configs "$(awk -F'\t' '$1 == "gtk-rgba8-window" { print $2 }' "$EVIDENCE/egl-choose-config.txt")"

  harness_gate ordinary none 7

  cc -shared -fPIC -o "$HOME/fixture.so" /repo/agterm-linux/tests/login_shell_zsh_fixture.c -ldl
  record login_shell "$(getent passwd "$USER" | cut -d: -f7)"
  harness_gate live live 9 LD_PRELOAD="$HOME/fixture.so"

  local run passes=0 probe_parent="$EVIDENCE/probe"
  install -d -m 0700 "$probe_parent"
  for run in $(seq 1 "$PROBE_RUNS"); do
    local status=0
    python3 -I /repo/scripts/omarchy-config-probe.py --payload "$(readlink -f "$PREFIX/lib/agterm")" \
      --output-dir "$probe_parent/run-$run" --app-sha256 "$app_sha" > "$probe_parent/run-$run.json" 2>&1 || status=$?
    (( status == 0 )) && passes=$((passes + 1))
    record "probe_run_$run" "exit=$status"
  done
  record probe "$([[ $passes == "$PROBE_RUNS" ]] && echo PASS || echo FAIL) $passes/$PROBE_RUNS"
  python3 -I - "$probe_parent" "$PROBE_RUNS" > "$EVIDENCE/probe-timings.tsv" <<'PY'
import json, pathlib, sys
for run in range(1, int(sys.argv[2]) + 1):
    try:
        report = json.loads((pathlib.Path(sys.argv[1]) / f'run-{run}' / 'report.json').read_text())
    except (OSError, ValueError):
        print(f'{run}\tno report'); continue
    shutdowns = [round(app.get('shutdown_seconds', -1), 3) for app in report.get('apps', [])]
    print(f"{run}\tpassed={report.get('passed')}\tchecks={len(report.get('checks', []))}\t"
          f"forced_recovery={report.get('forced_recovery')}\tshutdown_s={shutdowns}\terrors={report.get('errors')}")
PY

  local status=0
  /repo/scripts/install-linux.sh --prefix "$PREFIX" --uninstall --purge-state > "$EVIDENCE/uninstall.log" 2>&1 || status=$?
  # The row is FAIL unless the inspection itself ran; its exit status only adds that FAIL to the log.
  record uninstall "$(python3 -I /repo/.github/omarchy-x86-native/inspect-uninstall.py --prefix "$PREFIX" --user "$USER" \
    --uninstall-status "$status" --evidence "$EVIDENCE" || true)"

  cp "$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/hyprland.log" "$EVIDENCE/hyprland-runtime.log" 2>/dev/null || true
  grep -iE 'GL_RENDERER|GL_VENDOR|EGL|llvmpipe|renderer' "$EVIDENCE/hyprland-runtime.log" | head -n 20 >> "$EVIDENCE/hyprland-info.txt" || true
  stop_hyprland
  cat "$EVIDENCE/gates.tsv"
  ! grep -qP '^(ordinary|live|probe|uninstall)\tFAIL' "$EVIDENCE/gates.tsv"
}

case "$PHASE" in
  root) root_phase ;;
  setup) setup_phase ;;
  session) session_phase ;;
  *) echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
