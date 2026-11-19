#!/usr/bin/env bash
# Run the native Omarchy gates (ordinary, Live, config probe x10, uninstall) against one architecture's
# archive inside an Arch Linux (x86_64) or Arch Linux ARM (aarch64) container on a same-architecture host
# whose vkms module supplies a KMS device.
# Called by .github/workflows/omarchy-x86-native.yml as root in the container; drops to an unprivileged
# bash-login user for everything except package installation.
# Mounts: /repo (read-only checkout), /release (verified archive + SHA256SUMS), /out.
# GATE_ARCHIVE names the archive in /release.
set -euo pipefail
PHASE="${1:-root}"
SELF="$(readlink -f "$0")"
USER_NAME=omarchy
PREFIX_NAME=agt-prefix
ARCHIVE="${GATE_ARCHIVE:?GATE_ARCHIVE names the archive in /release}"
PROBE_RUNS="${PROBE_RUNS:-10}"

# Omarchy's desktop-runtime packages that are in the official Arch repositories, from
# basecamp/omarchy install/omarchy-base.packages at OMARCHY_REF. Omarchy-repo/AUR packages (walker, aether,
# omarchy-nvim, ...) and unrelated applications (office, media, browsers, docker) are left out.
OMARCHY_REF=3b36b9e19ea00a6c21492098308a7f44a8091564
OMARCHY_PACKAGES=(hyprland hyprland-guiutils hypridle hyprlock hyprpicker hyprsunset xdg-desktop-portal-hyprland
  xdg-desktop-portal-gtk uwsm mako waybar swaybg swayosd wl-clipboard grim slurp imagemagick fcitx5 fcitx5-gtk
  gnome-keyring gnome-themes-extra yaru-icon-theme noto-fonts noto-fonts-emoji ttf-jetbrains-mono-nerd
  fontconfig polkit-gnome xdg-terminal-exec bash-completion python-gobject jq libsecret)
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

root_phase() {
  local owner
  owner="$(stat -c %u:%g /out)"
  grep -E '^(ID|NAME|BUILD_ID)=' /etc/os-release > /out/base-os.txt
  prepare_base
  pacman_retry -Syu --noconfirm
  local available=() package
  for package in "${OMARCHY_PACKAGES[@]}"; do
    if pacman -Si "$package" >/dev/null 2>&1; then available+=("$package"); else echo "$package" >> /out/omarchy-packages-unavailable.txt; fi
  done
  pacman_retry -S --noconfirm --needed "${available[@]}" "${RUNTIME_PACKAGES[@]}"
  pacman -Q > /out/packages.txt
  echo "omarchy package list: basecamp/omarchy@$OMARCHY_REF install/omarchy-base.packages" > /out/omarchy-ref.txt
  useradd --create-home --uid 1000 --shell /bin/bash "$USER_NAME"
  # The container's own /dev nodes; the host's permissions are unchanged.
  chown "$USER_NAME" /dev/dri/card* /dev/dri/renderD* 2>/dev/null || true
  install -d -m 0700 -o "$USER_NAME" /run/user/1000
  install -d -m 0700 -o "$USER_NAME" "/home/$USER_NAME/evidence"
  status=0
  runuser -u "$USER_NAME" -- env -i PATH=/usr/local/bin:/usr/bin HOME="/home/$USER_NAME" USER="$USER_NAME" \
    LOGNAME="$USER_NAME" SHELL=/bin/bash LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/1000 PROBE_RUNS="$PROBE_RUNS" \
    GATE_ARCHIVE="$ARCHIVE" \
    dbus-run-session -- bash "$SELF" session || status=$?
  # Copying "evidence/." would also copy its 0700 mode and owner onto /out itself.
  cp -a "/home/$USER_NAME/evidence/." /out/
  chown -R "$owner" /out
  chmod -R u+rwX,go+rX /out
  exit "$status"
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
  grep -iE "GL_RENDERER|renderer|llvmpipe|softpipe" "$EVIDENCE/hyprland.log" | head -n 20 >> "$EVIDENCE/hyprland-info.txt" || true
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
  # The installer adds its renderer marker and manifests; every payload file must be byte-identical.
  diff -r "$stage/agterm-stage" "$PREFIX/lib/agterm" > "$EVIDENCE/installed-vs-stage.diff" || true
  record installed_vs_stage "$(grep -vcE '^Only in .*/share/agterm: (RENDERER|INSTALLED_FILES|INSTALLED_DIRS)$' \
    "$EVIDENCE/installed-vs-stage.diff" || true) unexpected differences"
  local app_sha
  app_sha="$(sha256sum "$PREFIX/lib/agterm/bin/agterm-linux.bin" | cut -d' ' -f1)"
  record app_sha256 "$app_sha"
  record renderer "$(cat "$PREFIX/lib/agterm/share/agterm/RENDERER")"
  for file in scripts/test-omarchy.sh scripts/omarchy-config-probe.py agterm-linux/tests/login_shell_zsh_fixture.c; do
    record "sha256:$file" "$(sha256sum "/repo/$file" | cut -d' ' -f1)"
  done
  record harness_blob "$(git -C /repo rev-parse HEAD:scripts/test-omarchy.sh)"

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
  { find "$PREFIX" -mindepth 1 -printf '%y %P\n' 2>/dev/null || true; } > "$EVIDENCE/uninstall-left.txt"
  { pgrep -u "$USER" -a -f 'agterm|zmx' || true; } | { grep -v 'omarchy-x86-native' || true; } > "$EVIDENCE/uninstall-stray.txt"
  local left stray
  left="$(wc -l < "$EVIDENCE/uninstall-left.txt")"
  stray="$(wc -l < "$EVIDENCE/uninstall-stray.txt")"
  record uninstall "$([[ $status == 0 && $left == 0 && $stray == 0 ]] && echo PASS || echo FAIL) exit=$status files_left=$left stray_processes=$stray"

  cp "$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/hyprland.log" "$EVIDENCE/hyprland-runtime.log" 2>/dev/null || true
  grep -iE 'GL_RENDERER|GL_VENDOR|EGL|llvmpipe|renderer' "$EVIDENCE/hyprland-runtime.log" | head -n 20 >> "$EVIDENCE/hyprland-info.txt" || true
  kill "$HYPRLAND_PID" 2>/dev/null || true
  wait "$HYPRLAND_PID" 2>/dev/null || true
  cat "$EVIDENCE/gates.tsv"
  ! grep -qP '^(ordinary|live|probe|uninstall)\tFAIL' "$EVIDENCE/gates.tsv"
}

case "$PHASE" in
  root) root_phase ;;
  session) session_phase ;;
  *) echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
