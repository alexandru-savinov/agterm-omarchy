#!/usr/bin/env bash
# Runs as root inside a disposable archlinux container on a hosted x86_64 runner.
# Installs an Omarchy-like Wayland stack, records versions, probes the bundled zmx,
# then runs the unmodified harness as an unprivileged user under headless Hyprland.
# Hosted runner + container + software rendering: NOT native Omarchy hardware.
set -uo pipefail
OUT=/work/out
STAGE=/work/agterm-stage
mkdir -p "$OUT"

echo "== container os"; cat /etc/os-release | tee "$OUT/os-release.txt"
uname -a | tee "$OUT/container-uname.txt"

echo "== install packages"
pacman -Syu --noconfirm --needed \
  hyprland grim imagemagick python zsh dbus mesa mesa-utils gtk4 libadwaita webkitgtk-6.0 \
  fontconfig ttf-dejavu file procps-ng which seatd >"$OUT/pacman-install.log" 2>&1
prc=$?
echo "pacman exit: $prc" | tee -a "$OUT/pacman-install.log"
if [[ $prc -ne 0 ]]; then
  tail -n 20 "$OUT/pacman-install.log"
  echo "package-install-failed" >"$OUT/compositor-status.txt"
  chown -R "${HOST_UID:-1001}:${HOST_GID:-1001}" "$OUT"
  exit 2
fi
pacman -Q hyprland aquamarine gtk4 glib2 fontconfig mesa libadwaita webkitgtk-6.0 grim imagemagick \
  python zsh wayland libglvnd libepoxy 2>&1 | tee "$OUT/packages.txt"

echo "== payload dependency resolution"
for elf in "$STAGE/bin/agterm-linux.bin" "$STAGE/bin/agtermctl.bin" "$STAGE/lib/libghostty.so"; do
  echo "--- $elf"
  LD_LIBRARY_PATH="$STAGE/lib" ldd "$elf"
done >"$OUT/ldd.txt" 2>&1
echo "ldd 'not found' count: $(grep -c 'not found' "$OUT/ldd.txt")" | tee "$OUT/ldd-missing-count.txt"
grep 'not found' "$OUT/ldd.txt" | sort -u || true
file "$STAGE/bin/zmx" | tee "$OUT/zmx-file.txt"

echo "== direct zmx probe (exit 132 would be SIGILL, the #52 class)"
mkdir -p /tmp/zmx-probe && chmod 700 /tmp/zmx-probe
ZMX_DIR=/tmp/zmx-probe timeout 20 "$STAGE/bin/zmx" list >"$OUT/zmx-probe.txt" 2>&1
zrc=$?
echo "zmx list exit: $zrc" | tee -a "$OUT/zmx-probe.txt"
echo "$zrc" >"$OUT/zmx-probe-exit.txt"

echo "== DRM devices visible in the container"
for card in /sys/class/drm/card[0-9]; do
  # Kernel 7.x registers vkms as a faux device (driver faux_driver, device name vkms).
  drv=$(basename "$(readlink -f "$card/device/driver")" 2>/dev/null)
  dev=$(basename "$(readlink -f "$card/device")" 2>/dev/null)
  echo "$(basename "$card") driver=$drv device=$dev"
done | tee "$OUT/container-drm.txt"
VKMS_CARD=$(awk '$2 == "driver=vkms" || $3 == "device=vkms" {print "/dev/dri/" $1; exit}' "$OUT/container-drm.txt")
echo "vkms card: ${VKMS_CARD:-none}" | tee -a "$OUT/container-drm.txt"
# Device nodes carry the host's video GID; open only the vkms node to the container test user.
[[ -n "$VKMS_CARD" ]] && chmod 0666 "$VKMS_CARD"
find /dev/dri -mindepth 1 -ls | tee -a "$OUT/container-drm.txt"

echo "== unprivileged test account (login shell zsh; disposable container account)"
useradd -m -s /usr/bin/zsh -G video tester
chown -R tester:tester "$OUT"
install -d -m 700 -o tester -g tester /tmp/xdg-tester
runuser -u tester -- env -i PATH=/usr/local/bin:/usr/bin HOME=/home/tester USER=tester LOGNAME=tester \
  SHELL=/usr/bin/zsh LANG=C.UTF-8 XDG_RUNTIME_DIR=/tmp/xdg-tester VKMS_CARD="$VKMS_CARD" \
  dbus-run-session -- bash /work/helpers/session.sh
src=$?
echo "session exit: $src" | tee "$OUT/session-exit.txt"
# Hand the results back to the runner user that summarizes and uploads them.
chown -R "${HOST_UID:-1001}:${HOST_GID:-1001}" "$OUT"
exit "$src"
