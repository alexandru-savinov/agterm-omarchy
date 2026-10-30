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
  fontconfig ttf-dejavu file procps-ng which >"$OUT/pacman-install.log" 2>&1
echo "pacman exit: $?" | tee -a "$OUT/pacman-install.log"
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

echo "== unprivileged test account (login shell zsh; disposable container account)"
useradd -m -s /usr/bin/zsh tester
chown -R tester:tester "$OUT"
install -d -m 700 -o tester -g tester /tmp/xdg-tester
runuser -u tester -- env -i PATH=/usr/local/bin:/usr/bin HOME=/home/tester USER=tester LOGNAME=tester \
  SHELL=/usr/bin/zsh LANG=C.UTF-8 XDG_RUNTIME_DIR=/tmp/xdg-tester \
  dbus-run-session -- bash /work/helpers/session.sh
src=$?
echo "session exit: $src" | tee "$OUT/session-exit.txt"
exit "$src"
