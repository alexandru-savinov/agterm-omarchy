#!/usr/bin/env bash
set -euo pipefail

probe=.github/omarchy-native-feel-probe
trap 'docker rm -f native-feel-desktop native-feel-keyboard >/dev/null 2>&1 || true' EXIT
[[ "$(git -C probe-omarchy rev-parse HEAD)" == "$OMARCHY_REF" ]]
printf 'Omarchy source: %s\n' "$OMARCHY_REF"

input_args=()
if [[ -c /dev/uinput ]]; then
  cc -std=c11 -Wall -Wextra -Werror -static "$probe/keyboard.c" -o "$RUNNER_TEMP/native-feel-keyboard"
fi
if [[ ! -c /dev/uinput ]]; then
  echo 'uinput host device absent'
elif docker run -d --name native-feel-keyboard --network none --device /dev/uinput:rw \
    -v "$RUNNER_TEMP/native-feel-keyboard:/keyboard:ro" "$BASE_IMAGE" bash -euc '
      chown 1000:1000 /dev/uinput
      exec setpriv --reuid=1000 --regid=1000 --clear-groups /keyboard'; then
  for (( attempt=0; attempt<40; attempt++ )); do
    keyboard="$(docker logs native-feel-keyboard 2>/dev/null | sed -nE 's/^keyboard-created=(input[0-9]+)$/\1/p')"
    [[ -n "$keyboard" ]] && break
    [[ "$(docker inspect --format '{{.State.Running}}' native-feel-keyboard)" == true ]] || break
    sleep 0.5
  done
  docker logs native-feel-keyboard
  if [[ "$keyboard" =~ ^input[0-9]+$ ]]; then
    for (( attempt=0; attempt<40; attempt++ )); do
      for sys_event in /sys/devices/virtual/input/"$keyboard"/event[0-9]*; do
        event="/dev/input/${sys_event##*/}"
        if [[ -c "$event" ]] && udevadm info -q property -n "$event" | grep -x 'ID_INPUT_KEYBOARD=1'; then
          # libinput opens keyboards O_RDWR. This grant covers only the fixture device.
          input_args=(--device "$event:$event:rw" -e "KEYBOARD_EVENT=$event")
          break 2
        fi
      done
      sleep 0.5
    done
  fi
else
  echo 'uinput injector container could not start'
fi

docker run -d --name native-feel-desktop --device /dev/dri "${input_args[@]}" \
  -v /run/udev:/run/udev:ro -v "$GITHUB_WORKSPACE:/repo:ro" --tmpfs /out \
  "$BASE_IMAGE" bash "/repo/$probe/desktop.sh"
docker logs --follow native-feel-desktop &
log_pid=$!
if (( ${#input_args[@]} )); then
  # Wait until Hyprland sees the specific keyboard, then send one real event pair.
  for (( attempt=0; attempt<210; attempt++ )); do
    if docker exec native-feel-desktop test -f /tmp/native-feel-keyboard-ready 2>/dev/null; then
      docker kill --signal=USR1 native-feel-keyboard
      break
    fi
    [[ "$(docker inspect --format '{{.State.Running}}' native-feel-desktop)" == true ]] || break
    sleep 5
  done
fi
status="$(docker wait native-feel-desktop)"
wait "$log_pid"
if (( ${#input_args[@]} )); then docker logs native-feel-keyboard; fi
exit "$status"
