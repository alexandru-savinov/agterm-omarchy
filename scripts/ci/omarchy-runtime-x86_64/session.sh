#!/usr/bin/env bash
# Runs as the unprivileged test user inside dbus-run-session: start headless Hyprland,
# then run the unmodified harness in ordinary (none) and Live mode against the payload.
set -uo pipefail
OUT=/work/out
STAGE=/work/agterm-stage
export LIBGL_ALWAYS_SOFTWARE=1
export AQ_TRACE=1 HYPRLAND_TRACE=1
if [[ -n "${VKMS_CARD:-}" ]]; then
  # Iteration 2: aquamarine's headless backend alone found no allocator in iteration 1
  # (CBackend::create() failed, no GPU). Give it the virtual KMS device instead, opened
  # directly through libseat's noop backend (test user is in the video group).
  export AQ_DRM_DEVICES="$VKMS_CARD" LIBSEAT_BACKEND=noop
  echo "compositor backend: DRM on vkms $VKMS_CARD (libseat noop)"
else
  export HYPRLAND_HEADLESS_ONLY=1
  echo "compositor backend: headless only (no vkms device)"
fi

mkdir -p "$HOME/.config/hypr"
cp /work/helpers/hyprland.lua "$HOME/.config/hypr/hyprland.lua"
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
  grep -v -E '^ +[a-zA-Z]+ *[a-zA-Z]*$' "$OUT/hyprland-stdout.log" | tail -n 60
  find "$XDG_RUNTIME_DIR" -maxdepth 3 -name 'hyprland.log' -exec cp {} "$OUT/hyprland.log" \;
  tail -n 80 "$OUT/hyprland.log"
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
hyprctl configerrors >"$OUT/hyprctl-configerrors.txt" 2>&1
hyprctl clients -j >"$OUT/hyprctl-clients-initial.json" 2>&1
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
