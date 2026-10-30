#!/usr/bin/env bash
# Runs INSIDE an Arch Linux ARM container (root). Installs a headless Hyprland stack,
# then runs the unmodified harness (scripts/test-omarchy.sh at the pinned ref) against the
# staged payload in ordinary and Live mode. If Hyprland cannot start, it records why and
# runs the compositor-neutral live-probe.py under headless weston instead, with every
# Hyprland-dependent check reported NOT RUN.
# Usage: arch-hyprland.sh <stage-dir> <harness-checkout> <probe-dir> <out-dir>
set -uo pipefail
stage="$(readlink -f "${1:?stage}")"
harness="$(readlink -f "${2:?harness checkout}")"
probe="$(readlink -f "${3:?probe dir}")"
out="$(readlink -f "${4:?out}")"
mkdir -p "$out"
status="$out/status.tsv"
printf 'step\tresult\tdetail\n' > "$status"
record() { printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" | tee -a "$status"; }

# --- packages ---------------------------------------------------------------------------
sed -i 's/^CheckSpace/#CheckSpace/' /etc/pacman.conf
sandbox=()
pacman --help 2>&1 | grep -q -- '--disable-sandbox' && sandbox=(--disable-sandbox)
pacman-key --init > "$out/pacman-key.log" 2>&1
pacman-key --populate archlinuxarm >> "$out/pacman-key.log" 2>&1
pkgs=(hyprland grim imagemagick gtk4 libadwaita webkitgtk-6.0 python mesa vulkan-icd-loader
      ttf-dejavu dbus weston file procps-ng util-linux which jq)
if pacman -Syu --noconfirm --needed "${sandbox[@]}" "${pkgs[@]}" > "$out/pacman.log" 2>&1; then
  record pacman ok "$(pacman -Q hyprland aquamarine gtk4 glib2 fontconfig mesa webkitgtk-6.0 libadwaita weston 2>&1 | tr '\n' ';')"
else
  record pacman FAIL "see pacman.log tail: $(tail -3 "$out/pacman.log" | tr '\n' ' ' | cut -c1-200)"
  exit 0
fi
pacman -Q > "$out/pacman-Q.txt"
grep -E '^(NAME|PRETTY_NAME|BUILD_ID)=' /etc/os-release > "$out/os-release.txt" 2>&1
uname -a > "$out/uname.txt"

{ echo "## ldd agterm-linux.bin (payload lib dir first, as the wrapper does)"
  LD_LIBRARY_PATH="$stage/lib" ldd "$stage/bin/agterm-linux.bin"
} > "$out/ldd.txt" 2>&1
missing="$(grep -c 'not found' "$out/ldd.txt")"
record ldd "$([[ $missing == 0 ]] && echo ok || echo FAIL)" "unresolved=$missing"

# --- unprivileged user, runtime dir, devices -------------------------------------------
id ci > /dev/null 2>&1 || useradd -m -G video,render,input ci 2>/dev/null || useradd -m ci
xdg=/tmp/xdg-ci
mkdir -p "$xdg" && chown ci: "$xdg" && chmod 700 "$xdg"
ls -la /dev/dri > "$out/dev-dri.txt" 2>&1 || echo "no /dev/dri" > "$out/dev-dri.txt"
chmod a+rw /dev/dri/* 2>/dev/null || true
chmod -R a+rX "$stage" "$harness" "$probe"
chown -R ci: "$out"
as_ci() { runuser -u ci -- env -i PATH=/usr/bin:/bin HOME=/home/ci USER=ci LANG=C.UTF-8 \
  XDG_RUNTIME_DIR="$xdg" "$@"; }

# --- Hyprland headless --------------------------------------------------------------------
card="$(find /dev/dri -maxdepth 1 -name 'card*' 2>/dev/null | sort | head -1)"
as_ci bash -c "cd /home/ci && HYPRLAND_HEADLESS_ONLY=1 ${card:+AQ_DRM_DEVICES=$card} LIBSEAT_BACKEND=noop \
  LIBGL_ALWAYS_SOFTWARE=1 AQ_TRACE=1 HYPRLAND_TRACE=1 dbus-run-session -- Hyprland > '$out/hyprland.log' 2>&1 &"
sig=""
for _ in $(seq 1 60); do
  sig="$(as_ci hyprctl instances -j 2>/dev/null | jq -r '.[0].instance // empty' 2>/dev/null)"
  [[ -n "$sig" ]] && break
  sleep 1
done
if [[ -n "$sig" ]]; then
  wl="$(as_ci hyprctl instances -j | jq -r '.[0].wl_socket')"
  if ! as_ci env HYPRLAND_INSTANCE_SIGNATURE="$sig" hyprctl monitors -j | jq -e 'length > 0' > /dev/null; then
    as_ci env HYPRLAND_INSTANCE_SIGNATURE="$sig" hyprctl output create headless >> "$out/hyprctl.log" 2>&1
    sleep 2
  fi
  as_ci env HYPRLAND_INSTANCE_SIGNATURE="$sig" hyprctl monitors -j > "$out/hyprctl-monitors.json" 2>&1
  as_ci env HYPRLAND_INSTANCE_SIGNATURE="$sig" hyprctl version > "$out/hyprctl-version.txt" 2>&1
  record hyprland ok "instance started, wl_socket=$wl, monitors=$(jq length "$out/hyprctl-monitors.json" 2>/dev/null)"
  for mode in none live; do
    dest="$out/harness-$mode"
    # The harness refuses non-empty artifact dirs; it creates this one itself.
    as_ci env HYPRLAND_INSTANCE_SIGNATURE="$sig" WAYLAND_DISPLAY="$wl" \
      AGTERM_TEST_BIN="$stage/bin/agterm-linux" AGTERM_TEST_CTL="$stage/bin/agtermctl" \
      AGTERM_TEST_RESTORE_MODE="$mode" AGTERM_OMARCHY_ARTIFACTS="$dest" \
      timeout --kill-after=10 300 dbus-run-session -- bash "$harness/scripts/test-omarchy.sh" \
      > "$out/harness-$mode.stdout" 2>&1
    code=$?
    passed="$(jq -r '.passed' "$dest/result.json" 2>/dev/null || echo missing)"
    record "harness-$mode" "$([[ $code == 0 && $passed == true ]] && echo PASS || echo FAIL)" \
      "exit=$code result.passed=$passed $(tail -2 "$out/harness-$mode.stdout" | tr '\n' ' ' | cut -c1-240)"
  done
  pkill -u ci Hyprland || true
else
  record hyprland "NOT RUN" "Hyprland did not start within 60 s; harness checks NOT RUN; log tail: $(tail -4 "$out/hyprland.log" | tr '\n' ' ' | cut -c1-300)"
fi

# --- compositor-neutral fallback / cross-check under headless weston ------------------
as_ci bash -c "weston --backend=headless --renderer=pixman --socket=wayland-weston --width=1280 --height=800 \
  --idle-time=0 --shell=kiosk > '$out/weston.log' 2>&1 &"
for _ in $(seq 1 30); do [[ -S "$xdg/wayland-weston" ]] && break; sleep 1; done
if [[ -S "$xdg/wayland-weston" ]]; then
  record weston ok "$(weston --version 2>&1)"
  for mode in none live; do
    as_ci env WAYLAND_DISPLAY=wayland-weston PROBE_BIN="$stage/bin/agterm-linux" PROBE_CTL="$stage/bin/agtermctl" \
      PROBE_MODE="$mode" PROBE_LABEL="arch-weston-$mode" PROBE_OUT="$out/probe-weston-$mode" \
      timeout --kill-after=10 240 dbus-run-session -- python3 -I "$probe/live-probe.py" > "$out/probe-weston-$mode.stdout" 2>&1
    passed="$(jq -r '.passed' "$out/probe-weston-$mode/result.json" 2>/dev/null || echo missing)"
    record "probe-weston-$mode" "$([[ $passed == true ]] && echo PASS || echo FAIL)" \
      "$(jq -c '{checks, failure}' "$out/probe-weston-$mode/result.json" 2>/dev/null | cut -c1-300)"
  done
  pkill -u ci weston || true
else
  record weston FAIL "socket not created; $(tail -3 "$out/weston.log" | tr '\n' ' ' | cut -c1-200)"
fi
chmod -R a+rX "$out"
exit 0
