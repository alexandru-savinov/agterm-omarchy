#!/usr/bin/env bash
# Runs on the ubuntu-24.04-arm host. Starts headless weston (Hyprland is exercised in the
# arch-hyprland job) and runs live-probe.py against:
#   - the unchanged payload, restoreMode none and live;
#   - diagnostic COPIES whose bin/zmx is a wrapper that runs the unchanged zmx bytes under
#     qemu-aarch64 with a given CPU model (restricted model reproduces/refutes the VM
#     symptom; "max" is the emulation control). The original payload is never modified.
# Usage: ubuntu-live.sh <stage-dir> <probe-dir> <out-dir>
set -uo pipefail
stage="$(readlink -f "${1:?stage}")"
probe="$(readlink -f "${2:?probe dir}")"
out="$(readlink -f "${3:?out}")"
mkdir -p "$out"
status="$out/status.tsv"
printf 'step\tresult\tdetail\n' > "$status"
record() { printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" | tee -a "$status"; }

{ echo "## ldd agterm-linux.bin (payload lib dir first, as the wrapper does)"
  LD_LIBRARY_PATH="$stage/lib" ldd "$stage/bin/agterm-linux.bin"
} > "$out/ldd.txt" 2>&1
missing="$(grep -c 'not found' "$out/ldd.txt")"
record ldd "$([[ $missing == 0 ]] && echo ok || echo FAIL)" "unresolved=$missing"
dpkg-query -W -f='${Package} ${Version}\n' weston libgtk-4-1 libadwaita-1-0 libwebkitgtk-6.0-4 \
  libglib2.0-0t64 libfontconfig1 libegl-mesa0 qemu-user > "$out/packages.txt" 2>&1 || true

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [[ ! -d "$XDG_RUNTIME_DIR" || ! -w "$XDG_RUNTIME_DIR" ]]; then
  export XDG_RUNTIME_DIR="$RUNNER_TEMP/xdg"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
fi
weston_n=0
start_weston() {
  weston_n=$((weston_n + 1))
  rm -f "$XDG_RUNTIME_DIR/wayland-weston" "$XDG_RUNTIME_DIR/wayland-weston.lock"
  weston --backend=headless --renderer=pixman --socket=wayland-weston --width=1280 --height=800 \
    --idle-time=0 --shell=kiosk > "$out/weston-$weston_n.log" 2>&1 &
  weston_pid=$!
  for _ in $(seq 1 30); do [[ -S "$XDG_RUNTIME_DIR/wayland-weston" ]] && return 0; sleep 1; done
  return 1
}
if ! start_weston; then
  record weston FAIL "socket not created; $(tail -3 "$out/weston-$weston_n.log" | tr '\n' ' ' | cut -c1-200)"
  exit 0
fi
record weston ok "$(weston --version 2>&1)"
# Live mode requires zsh as the password-database login shell (README; ZmxSupport.swift).
record login-shell "$(getent passwd "$(id -un)" | cut -d: -f7)" "$(id -un)"

run_probe() {  # run_probe <label> <stage> <mode>
  local label="$1" st="$2" mode="$3"
  WAYLAND_DISPLAY=wayland-weston PROBE_BIN="$st/bin/agterm-linux" PROBE_CTL="$st/bin/agtermctl" \
    PROBE_MODE="$mode" PROBE_LABEL="$label" PROBE_OUT="$out/probe-$label" \
    timeout --kill-after=10 240 dbus-run-session -- python3 -I "$probe/live-probe.py" > "$out/probe-$label.stdout" 2>&1
  local passed; passed="$(jq -r '.passed' "$out/probe-$label/result.json" 2>/dev/null || echo missing)"
  record "probe-$label" "$([[ $passed == true ]] && echo PASS || echo FAIL)" \
    "$(jq -c '{checks, failure}' "$out/probe-$label/result.json" 2>/dev/null | cut -c1-300)"
  # Compositor liveness after each probe: a dead weston would fail the next probe for reasons
  # unrelated to the payload, so record it, restart it, and say so.
  if kill -0 "$weston_pid" 2>/dev/null; then
    record "weston-after-$label" alive "pid=$weston_pid instance=$weston_n"
  else
    wait "$weston_pid"; local rc=$?
    record "weston-after-$label" DEAD "pid=$weston_pid instance=$weston_n exit=$rc log tail: $(tail -3 "$out/weston-$weston_n.log" | tr '\n' ' ' | cut -c1-200)"
    start_weston || { record weston FAIL "restart failed"; exit 0; }
    record weston restarted "instance=$weston_n"
  fi
}
run_probe_retry() {  # rerun once on a fresh compositor if the first attempt failed
  run_probe "$@"
  if [[ "$(jq -r '.passed' "$out/probe-$1/result.json" 2>/dev/null)" != true ]]; then
    kill "$weston_pid" 2>/dev/null; wait "$weston_pid" 2>/dev/null
    start_weston || { record weston FAIL "restart failed"; exit 0; }
    record weston restarted "instance=$weston_n (fresh compositor for retry of $1)"
    run_probe "$1-retry" "$2" "$3"
  fi
}

run_probe_retry ubuntu-weston-none "$stage" none
run_probe_retry ubuntu-weston-live "$stage" live

copies="$RUNNER_TEMP/diagnostic-copies"
mkdir -p "$copies"
here="$(dirname "$(readlink -f "$0")")"
for model in ${QEMU_LIVE_MODELS:-neoverse-n1 max max,sve=off cortex-a72}; do
  safe="${model//[^a-z0-9]/-}"
  if bash "$here/make-qemu-copy.sh" "$stage" "$copies/stage-qemu-$safe" "$model" "$(command -v qemu-aarch64)" >> "$out/copies.log" 2>&1; then
    run_probe_retry "ubuntu-weston-live-qemu-$safe" "$copies/stage-qemu-$safe" live
  else
    record "copy-$safe" FAIL "diagnostic copy failed; see copies.log"
  fi
done

kill "$weston_pid" 2>/dev/null || true
exit 0
