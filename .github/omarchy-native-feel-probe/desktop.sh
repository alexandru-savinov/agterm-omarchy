#!/usr/bin/env bash
set -euo pipefail

report() { printf '%s\t%s\n' "$1" "$2"; }

if [[ ${1:-} != session ]]; then
  [[ ! -e /dev/uinput ]]
  report uinput_without_mapping absent
  if ! timeout --kill-after=30s 15m env GATE_ARCHIVE=unused OMARCHY_CHANNEL=stable \
    bash /repo/.github/omarchy-x86-native/gates.sh setup; then
    tail -n 35 /out/*.log 2>/dev/null || true
    exit 1
  fi
  if [[ -f /out/omarchy-packages-unavailable.txt ]]; then
    cat /out/omarchy-packages-unavailable.txt
  fi
  # The gate has the selector. Add only a real terminal provider from the official snapshot.
  pacman -S --needed --noconfirm foot || report foot unavailable
  pacman -Q hyprland xdg-terminal-exec uwsm fontconfig ttf-jetbrains-mono-nerd foot || true
  useradd --create-home --uid 1000 --shell /bin/bash omarchy
  chown omarchy /dev/dri/card* /dev/dri/renderD* 2>/dev/null || true
  if [[ -n ${KEYBOARD_EVENT:-} ]]; then chown omarchy "$KEYBOARD_EVENT"; fi
  install -d -m 0700 -o omarchy /run/user/1000
  exec runuser -u omarchy -- env -i PATH=/usr/bin HOME=/home/omarchy USER=omarchy LOGNAME=omarchy \
    SHELL=/bin/bash LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/1000 KEYBOARD_EVENT="${KEYBOARD_EVENT:-}" \
    dbus-run-session -- bash "$0" session
fi

wait_for() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@"; do
    (( SECONDS < deadline )) || return 1
    sleep 0.25
  done
}

start_hyprland() {
  local config="$1" drm card
  for drm in /sys/class/drm/card[0-9]*; do
    if [[ "$(basename "$(readlink -f "$drm/device")")" == vkms ]]; then
      card="/dev/dri/${drm##*/}"
      break
    fi
  done
  if [[ -z ${card:-} ]]; then echo 'vkms card absent'; return 1; fi
  LIBSEAT_BACKEND=noop AQ_DRM_DEVICES="$card" HYPRLAND_NO_SD_NOTIFY=1 HYPRLAND_NO_SD_VARS=1 \
    Hyprland --config "$config" > "$HOME/hyprland.log" 2>&1 &
  hyprland_pid=$!
  wait_for 30 compositor_socket || return 1
  wait_for 30 monitor_ready
}

compositor_socket() {
  local socket display
  socket="$(find "$XDG_RUNTIME_DIR/hypr" -maxdepth 2 -type s -name .socket.sock -print -quit 2>/dev/null)" || return 1
  display="$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'wayland-*' -print -quit)"
  [[ -n "$socket" && -n "$display" ]] || return 1
  HYPRLAND_INSTANCE_SIGNATURE="$(basename "$(dirname "$socket")")"
  WAYLAND_DISPLAY="${display##*/}"
  export HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY
}

monitor_ready() { hyprctl -j monitors | jq -e 'length > 0' >/dev/null 2>&1; }

stop_hyprland() {
  if [[ -n ${hyprland_pid:-} ]]; then
    kill "$hyprland_pid" 2>/dev/null || true
    if ! wait_for 5 compositor_gone; then kill -KILL "$hyprland_pid" 2>/dev/null || true; fi
    wait "$hyprland_pid" 2>/dev/null || true
  fi
  # Hyprland can leave a runtime socket behind if it aborts.
  rm -f "$XDG_RUNTIME_DIR"/hypr/*/.socket.sock "$XDG_RUNTIME_DIR"/wayland-*
  unset hyprland_pid HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY
}

compositor_gone() { ! kill -0 "$hyprland_pid" 2>/dev/null; }
keyboard_seen() { hyprctl -j devices | jq -e '.keyboards[] | select(.name == "omarchy-native-feel-probe-keyboard")' >/dev/null; }
user_manager_ready() { systemctl --user show-environment >/dev/null 2>&1; }

try_uwsm_with_user_manager() {
  local manager status=0
  findmnt -n -o TARGET,FSTYPE,OPTIONS /sys/fs/cgroup
  systemd --user > "$HOME/systemd-user.log" 2>&1 &
  manager=$!
  if wait_for 8 user_manager_ready; then
    report systemd_user_manager PASS
    systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE PATH
    timeout 15s "$OMARCHY_PATH/bin/omarchy-launch-tui" touch /tmp/tui-with-user-manager || status=$?
    if wait_for 5 test -f /tmp/tui-with-user-manager; then
      report omarchy_launch_tui_with_manager PASS
    else
      report omarchy_launch_tui_with_manager "FAIL: exit=$status marker absent"
      systemctl --user status wayland-wm-app-daemon.service 2>&1 || true
    fi
  else
    report systemd_user_manager 'FAIL: no user manager in the existing container'
    systemctl --user status 2>&1 || true
  fi
  kill "$manager" 2>/dev/null || true
  wait "$manager" 2>/dev/null || true
  cat "$HOME/systemd-user.log"
}

trap stop_hyprland EXIT
export OMARCHY_PATH=/repo/probe-omarchy
mkdir -p "$HOME/.config" "$HOME/.local/state/omarchy/current" "$HOME/probe-bin"
cat > "$HOME/minimal.lua" <<'LUA'
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })
hl.config({ animations = { enabled = false }, ecosystem = { no_update_news = true, no_donation_nag = true } })
hl.bind("F8", hl.dsp.exec_cmd("touch /tmp/fired"))
LUA
if start_hyprland "$HOME/minimal.lua"; then
  hyprctl version
  hyprctl configerrors
  if [[ -z "$KEYBOARD_EVENT" ]]; then
    report uinput_bind 'unmeasured: no virtual event device mapped'
  elif wait_for 15 keyboard_seen; then
    [[ ! -e /tmp/fired ]]
    touch /tmp/native-feel-keyboard-ready
    if wait_for 15 test -f /tmp/fired; then report uinput_bind PASS; else report uinput_bind 'FAIL: marker absent'; fi
  else
    hyprctl -j devices
    report uinput_bind 'FAIL: Hyprland did not enumerate the keyboard'
  fi
  printf '%s\n' foot.desktop > "$HOME/.config/xdg-terminals.list"
  for launcher in xdg-terminal-exec omarchy-launch-tui; do
    status=0
    if [[ "$launcher" == xdg-terminal-exec ]]; then
      timeout 15s xdg-terminal-exec --print-id || true
      timeout 15s xdg-terminal-exec -e touch "/tmp/$launcher" || status=$?
    else
      timeout 15s "$OMARCHY_PATH/bin/$launcher" touch "/tmp/$launcher" || status=$?
    fi
    if wait_for 5 test -f "/tmp/$launcher"; then report "$launcher" PASS; else report "$launcher" "FAIL: exit=$status marker absent"; fi
  done
  try_uwsm_with_user_manager
else
  report minimal_hyprland 'FAIL: no ready vkms monitor'
fi
tail -n 50 "$HOME/hyprland.log"
stop_hyprland

cp -r "$OMARCHY_PATH/config/hypr" "$OMARCHY_PATH/config/foot" "$HOME/.config/"
# Preserve Omarchy's script behavior on its two scratch trees and icon scratch directory.
# Handle their cleanup with find to respect the probe's recursive rm restriction.
cat > "$HOME/probe-bin/rm" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == -rf && $# == 2 ]]; then
  current="$HOME/.local/state/omarchy/current"
  case "$2" in
    "$current/theme"|"$current/next-theme"|"$current"/.icons.??????)
      [[ ! -e "$2" && ! -L "$2" ]] || find "$2" -depth -delete
      exit "$?"
      ;;
  esac
fi
for arg in "$@"; do
  case "$arg" in -[rR]*|-[a-zA-Z]*[rR]*|--recursive) echo "recursive rm refused" >&2; exit 1 ;; esac
done
exec /usr/bin/rm "$@"
SH
chmod +x "$HOME/probe-bin/rm"
export PATH="$HOME/probe-bin:$OMARCHY_PATH/bin:$PATH"

timeout 30s env OMARCHY_THEME_HEADLESS=1 omarchy-theme-set 'Tokyo Night' > "$HOME/theme-headless.log" 2>&1 || true
current="$HOME/.local/state/omarchy/current"
if grep -qx tokyo-night "$current/theme.name" && [[ -s "$current/theme/hyprland.lua" && -f "$current/background" ]]; then
  report theme_files PASS
else
  report theme_files FAIL
fi
cat "$HOME/theme-headless.log"

if ! start_hyprland "$HOME/.config/hypr/hyprland.lua"; then
  report default_hyprland 'FAIL: no ready vkms monitor'
  tail -n 80 "$HOME/hyprland.log"
  exit 0
fi
errors="$(hyprctl configerrors)"
if [[ -z "$errors" ]]; then report default_hyprland PASS; else report default_hyprland FAIL; printf '%s\n' "$errors"; fi
hyprctl -j binds | jq 'length'
timeout 60s env OMARCHY_THEME_WAIT=1 omarchy-theme-set Nord > "$HOME/theme-live.log" 2>&1 || true
if grep -qx nord "$current/theme.name" && [[ -s "$current/theme/hyprland.lua" && -f "$current/background" ]]; then
  report live_theme_files PASS
else
  report live_theme_files FAIL
fi
if omarchy-shell shell ping; then report live_theme_shell available; else report live_theme_shell unavailable; fi
cat "$HOME/theme-live.log"
font='JetBrainsMono Nerd Font Mono'
timeout 75s omarchy-font-set "$font" > "$HOME/font.log" 2>&1 || true
if fc-match -f '%{family}\n' monospace | grep -Fq "$font" &&
   grep -qF "font=$font:size=" "$HOME/.config/foot/foot.ini"; then
  report font_files PASS
else
  report font_files FAIL
fi
cat "$HOME/font.log"
tail -n 80 "$HOME/hyprland.log"
