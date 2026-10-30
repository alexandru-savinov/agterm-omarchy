#!/usr/bin/env bash
# Runs as the unprivileged test user inside dbus-run-session: start headless Hyprland,
# then run the unmodified harness in ordinary (none) and Live mode against the payload.
set -uo pipefail
OUT=/work/out
STAGE=/work/agterm-stage
export LIBGL_ALWAYS_SOFTWARE=1
# Upstream Hyprland's hyprtester launches Hyprland this way for its headless test suite.
export HYPRLAND_HEADLESS_ONLY=1

Hyprland >"$OUT/hyprland-stdout.log" 2>&1 &
hpid=$!
sock=""
for _ in $(seq 1 120); do
  sock=$(find "$XDG_RUNTIME_DIR/hypr" -name .socket.sock 2>/dev/null | head -n1)
  [[ -n "$sock" ]] && break
  kill -0 "$hpid" 2>/dev/null || break
  sleep 0.5
done
if [[ -z "$sock" ]]; then
  echo "Hyprland did not create its IPC socket (pid alive: $(kill -0 "$hpid" 2>/dev/null && echo yes || echo no))"
  tail -n 60 "$OUT/hyprland-stdout.log"
  find "$XDG_RUNTIME_DIR" -maxdepth 3 -name 'hyprland.log' -exec cp {} "$OUT/hyprland.log" \;
  echo "hyprland-failed" >"$OUT/compositor-status.txt"
  exit 3
fi
HYPRLAND_INSTANCE_SIGNATURE=$(basename "$(dirname "$sock")")
export HYPRLAND_INSTANCE_SIGNATURE
for _ in $(seq 1 40); do
  wl=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'wayland-*' | head -n1)
  [[ -n "$wl" ]] && break
  sleep 0.5
done
export WAYLAND_DISPLAY="${wl##*/}"
echo "compositor up: signature set, WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
hyprctl version -j >"$OUT/hyprctl-version.json" 2>&1
if [[ "$(hyprctl monitors -j | python3 -I -c 'import json,sys; print(len(json.load(sys.stdin)))')" == 0 ]]; then
  hyprctl output create headless >"$OUT/hyprctl-output-create.txt" 2>&1
  sleep 2
fi
hyprctl monitors -j >"$OUT/hyprctl-monitors.json" 2>&1
echo "hyprland-running" >"$OUT/compositor-status.txt"
timeout 20 eglinfo -B -p wayland >"$OUT/eglinfo-wayland.txt" 2>&1 || true

for mode in none live; do
  echo "== harness restore_mode=$mode"
  AGTERM_TEST_BIN="$STAGE/bin/agterm-linux" AGTERM_TEST_CTL="$STAGE/bin/agtermctl" \
    AGTERM_TEST_RESTORE_MODE="$mode" AGTERM_OMARCHY_ARTIFACTS="$OUT/harness-$mode" \
    timeout 600 bash /work/test-omarchy.sh >"$OUT/harness-$mode.log" 2>&1
  rc=$?
  echo "$rc" >"$OUT/harness-$mode-exit.txt"
  echo "harness $mode exit: $rc"
  tail -n 25 "$OUT/harness-$mode.log"
done

hyprctl clients -j >"$OUT/hyprctl-clients-final.json" 2>&1
find "$XDG_RUNTIME_DIR/hypr" -name 'hyprland.log' -exec cp {} "$OUT/hyprland.log" \; 2>/dev/null
kill "$hpid" 2>/dev/null
wait "$hpid" 2>/dev/null
exit 0
