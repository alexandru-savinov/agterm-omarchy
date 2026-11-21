#!/bin/bash
# Installs the agterm-omarchy-bin package that bin/build made with pacman -U, inside the native gates'
# Arch container (Omarchy channel mirror and packages, Hyprland on vkms), runs the gates' ordinary and
# Live harness through /usr/bin, then removes it with pacman -R.
# The container setup, Hyprland session and EGL probe are gates.sh's own functions, sourced unchanged
# with its phase dispatch cut off, so this tests the package in exactly the gates' environment.
# Mounts: /repo (read-only checkout), /pkg (the package), /out. PKG_FILE names the package in /pkg.
# shellcheck disable=SC2154 # USER_NAME, SETUP_DEADLINE, SESSION_DEADLINE and HYPRLAND_PID come from gates.sh.
set -euo pipefail
PKG_FILE="${PKG_FILE:?PKG_FILE names the package in /pkg}"
PACKAGE=agterm-omarchy-bin
export GATE_ARCHIVE="$PKG_FILE" # gates.sh requires it; only its functions are used here.
# shellcheck disable=SC1090
source <(sed '/^case "\$PHASE" in$/,$d' /repo/.github/omarchy-x86-native/gates.sh)
declare -F setup_phase finish_root start_hyprland stop_hyprland record omarchy_mirror pacman_retry >/dev/null ||
  { echo "gates.sh no longer defines the functions this script reuses" >&2; exit 2; }
PHASE="${1:-root}"
SELF="$(readlink -f "$0")"

root_record() { printf '%s\t%s\n' "$1" "$2" | tee -a /out/install.tsv; }

install_package() {
  pacman -Qq | sort > /out/packages-before.txt
  local status=0
  pacman_retry -U --noconfirm "/pkg/$PKG_FILE" > /out/pacman-U.log 2>&1 || status=$?
  pacman -Qq | sort > /out/packages-after-install.txt
  comm -13 /out/packages-before.txt /out/packages-after-install.txt > /out/packages-added.txt
  if (( status != 0 )); then
    root_record pacman_U "FAIL exit=$status"
    return 1
  fi
  # Anything pacman added besides the package must be a dependency it pulled in, not an orphan.
  local added unexpected=()
  pacman -Qqd | sort > /out/packages-asdeps.txt
  pacman -Qqdt | sort > /out/packages-orphans.txt || true
  while read -r added; do
    [[ "$added" == "$PACKAGE" ]] && continue
    if ! grep -qxF "$added" /out/packages-asdeps.txt || grep -qxF "$added" /out/packages-orphans.txt; then
      unexpected+=("$added")
    fi
  done < /out/packages-added.txt
  local pulled
  pulled="$(grep -vxF "$PACKAGE" /out/packages-added.txt | tr '\n' ' ')"
  if (( ${#unexpected[@]} )) || ! grep -qxF "$PACKAGE" /out/packages-added.txt; then
    root_record pacman_U "FAIL unexpected: ${unexpected[*]:-$PACKAGE not installed}"
    return 1
  fi
  root_record pacman_U "PASS added $PACKAGE; dependencies pulled: ${pulled:-none}"
  root_record version "$(pacman -Q "$PACKAGE")"
  pacman -Qkk "$PACKAGE" > /out/pacman-Qkk.txt 2>&1 || true
  if grep -qE "^$PACKAGE: [0-9]+ total files, 0 altered files$" /out/pacman-Qkk.txt; then
    root_record files "PASS $(tail -1 /out/pacman-Qkk.txt)"
  else
    root_record files "FAIL $(tail -1 /out/pacman-Qkk.txt)"
    return 1
  fi
  local foreign
  foreign="$(find /usr/lib/agterm \( ! -user root -o ! -group root \) | wc -l)"
  (( foreign == 0 )) || { root_record ownership "FAIL $foreign entries not root:root"; return 1; }
  root_record ownership "PASS every /usr/lib/agterm entry root:root"
  root_record launchers "$(readlink /usr/bin/agterm-linux) $(readlink /usr/bin/agtermctl)"
}

remove_package() {
  pacman -Qlq "$PACKAGE" | grep -v '/$' > /out/installed-files.txt
  local status=0 left=0 file
  pacman -R --noconfirm "$PACKAGE" > /out/pacman-R.log 2>&1 || status=$?
  while read -r file; do
    if [[ -e "$file" || -L "$file" ]]; then left=$((left + 1)); echo "$file" >> /out/files-left.txt; fi
  done < /out/installed-files.txt
  pacman -Qq | sort > /out/packages-after-remove.txt
  local restored=false procs
  if diff <(grep -vxF "$PACKAGE" /out/packages-after-install.txt) /out/packages-after-remove.txt > /out/packages-remove.diff; then
    restored=true
  fi
  procs="$(pgrep -fc 'agterm-linux\.bin|/usr/lib/agterm/bin/zmx' || true)"
  if (( status == 0 && left == 0 )) && [[ $restored == true && $procs == 0 && ! -e /usr/lib/agterm ]]; then
    root_record uninstall "PASS pacman -R exit=0, $(wc -l < /out/installed-files.txt) files removed, 0 left, no agterm or zmx process"
  else
    root_record uninstall "FAIL exit=$status left=$left package_set_restored=$restored processes=$procs"
  fi
}

root_phase() {
  local owner status=0
  owner="$(stat -c %u:%g /out)"
  : > /out/install.tsv
  timeout --kill-after=30s "$SETUP_DEADLINE" bash "$SELF" setup || status=$?
  if (( status != 0 )); then
    root_record setup "FAIL exit=$status deadline=$SETUP_DEADLINE"
    finish_root "$owner" "$status"
  fi
  root_record omarchy_channel "$OMARCHY_CHANNEL $(omarchy_mirror)"
  root_record package_sha256 "$(sha256sum "/pkg/$PKG_FILE" | cut -d' ' -f1)"
  install_package || finish_root "$owner" 1
  useradd --create-home --uid 1000 --shell /bin/bash "$USER_NAME"
  chown "$USER_NAME" /dev/dri/card* /dev/dri/renderD* 2>/dev/null || true
  install -d -m 0700 -o "$USER_NAME" /run/user/1000
  install -d -m 0700 -o "$USER_NAME" "/home/$USER_NAME/evidence"
  timeout --kill-after=60s "$SESSION_DEADLINE" runuser -u "$USER_NAME" -- env -i PATH=/usr/local/bin:/usr/bin \
    HOME="/home/$USER_NAME" USER="$USER_NAME" LOGNAME="$USER_NAME" SHELL=/bin/bash LANG=C.UTF-8 \
    XDG_RUNTIME_DIR=/run/user/1000 OMARCHY_CHANNEL="$OMARCHY_CHANNEL" PKG_FILE="$PKG_FILE" \
    dbus-run-session -- bash "$SELF" session || status=$?
  (( status != 124 && status != 137 )) ||
    printf 'session\tFAIL deadline %s reached\n' "$SESSION_DEADLINE" >> "/home/$USER_NAME/evidence/gates.tsv"
  cp -a "/home/$USER_NAME/evidence/." /out/
  remove_package
  if grep -qP '\tFAIL' /out/install.tsv /out/gates.tsv || ! grep -qP '^ordinary\tPASS' /out/gates.tsv ||
    ! grep -qP '^live\tPASS' /out/gates.tsv; then
    status=1
  fi
  finish_root "$owner" "$status"
}

# The gates' harness_gate, with the installed /usr/bin launchers. A pacman install has no renderer marker,
# so the gates' --software-rendering becomes the documented LIBGL_ALWAYS_SOFTWARE=1.
harness() {
  local name="$1" mode="$2" expected="$3" gtk_configs="$4"; shift 4
  local status=0
  env "$@" LIBGL_ALWAYS_SOFTWARE=1 AGTERM_TEST_BIN=/usr/bin/agterm-linux AGTERM_TEST_CTL=/usr/bin/agtermctl \
    AGTERM_TEST_RESTORE_MODE="$mode" AGTERM_OMARCHY_ARTIFACTS="$EVIDENCE/$name" \
    /repo/scripts/test-omarchy.sh > "$EVIDENCE/$name.log" 2>&1 || status=$?
  python3 -I /repo/.github/omarchy-x86-native/harness-result.py \
    "$EVIDENCE/$name" "$expected" "$status" "$name" "$gtk_configs" >> "$EVIDENCE/gates.tsv"
  # Every app process the harness saw must be the installed binary.
  python3 -I - "$EVIDENCE/$name/result.json" "$(sha256sum /usr/lib/agterm/bin/agterm-linux.bin | cut -d' ' -f1)" \
    "$name" >> "$EVIDENCE/gates.tsv" <<'PY' || true
import json, sys
path, installed, name = sys.argv[1:]
try:
    apps = json.load(open(path))["identity"]["app_processes"]
except (OSError, ValueError, KeyError, TypeError) as error:
    print(f"{name}_binary\tFAIL no identity ({type(error).__name__})"); sys.exit()
seen = sorted({app.get("sha256") for app in apps})
print(f"{name}_binary\t{'PASS' if apps and seen == [installed] else 'FAIL'} {len(apps)} app processes, sha256 {seen}")
PY
}

session_phase() {
  EVIDENCE="$HOME/evidence"
  git config --global --add safe.directory /repo
  start_hyprland
  record mesa "$(pacman -Q mesa | cut -d' ' -f2)"
  record hyprland "$(pacman -Q hyprland | cut -d' ' -f2)"
  record app_sha256 "$(sha256sum /usr/lib/agterm/bin/agterm-linux.bin | cut -d' ' -f1)"
  record harness_blob "$(git -C /repo rev-parse HEAD:scripts/test-omarchy.sh)"
  local gtk_configs
  gtk_configs="$(awk -F'\t' '$1 == "gtk-rgba8-window" { print $2 }' "$EVIDENCE/egl-choose-config.txt")"
  record egl_gtk_configs "$gtk_configs"
  harness ordinary none 7 "$gtk_configs"
  cc -shared -fPIC -o "$HOME/fixture.so" /repo/agterm-linux/tests/login_shell_zsh_fixture.c -ldl
  record login_shell "$(getent passwd "$USER" | cut -d: -f7)"
  harness live live 9 "$gtk_configs" LD_PRELOAD="$HOME/fixture.so"
  stop_hyprland
  cat "$EVIDENCE/gates.tsv"
}

case "$PHASE" in
  root) root_phase ;;
  setup) setup_phase ;;
  session) session_phase ;;
  *) echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
