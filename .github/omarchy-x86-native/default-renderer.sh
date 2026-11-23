#!/usr/bin/env bash
# The default-renderer and N-1 -> N upgrade scenario on x86_64: two releases installed the README's way
# (verified archive, that tag's own install-linux.sh, prefix ~/.local) with --default-rendering, driven
# under Hyprland on vkms by default-renderer.py.
# Called by omarchy-x86-native.yml's default-renderer job as root in the Arch container. It reuses
# gates.sh's package setup and Hyprland functions; nothing here changes the four gates or their verdict.
# Mounts: /repo (read-only checkout), /dr (from/ and to/ release assets, installer-from/ and installer-to/
# checkouts at each release's source commit, read-only), /out.
set -euo pipefail
DR_PHASE="${1:-root}"
DR_SELF="$(readlink -f "$0")"
DR_DEADLINE=30m
export GATE_ARCHIVE="${DR_TO_ARCHIVE:?DR_TO_ARCHIVE names the N archive in /dr/to}"
: "${DR_FROM_ARCHIVE:?}" "${DR_FROM_VERSION:?}" "${DR_TO_VERSION:?}"
# shellcheck source=gates.sh
source "$(dirname "$DR_SELF")/gates.sh"

dr_root() {
  local owner status=0
  owner="$(stat -c %u:%g /out)"
  timeout --kill-after=30s "$SETUP_DEADLINE" bash "$DR_SELF" setup || status=$?
  if (( status != 0 )); then
    printf 'setup\tFAIL exit=%s deadline=%s\n' "$status" "$SETUP_DEADLINE" >> /out/default-renderer.tsv
    finish_root "$owner" "$status"
  fi
  useradd --create-home --uid 1000 --shell /bin/bash "$USER_NAME"
  chown "$USER_NAME" /dev/dri/card* /dev/dri/renderD* 2>/dev/null || true
  install -d -m 0700 -o "$USER_NAME" /run/user/1000
  install -d -m 0700 -o "$USER_NAME" "/home/$USER_NAME/evidence"
  timeout --kill-after=60s "$DR_DEADLINE" runuser -u "$USER_NAME" -- env -i PATH=/usr/local/bin:/usr/bin \
    HOME="/home/$USER_NAME" USER="$USER_NAME" LOGNAME="$USER_NAME" SHELL=/bin/bash LANG=C.UTF-8 \
    XDG_RUNTIME_DIR=/run/user/1000 OMARCHY_CHANNEL="$OMARCHY_CHANNEL" GATE_ARCHIVE="$GATE_ARCHIVE" \
    DR_FROM_ARCHIVE="$DR_FROM_ARCHIVE" DR_TO_ARCHIVE="$DR_TO_ARCHIVE" DR_FROM_VERSION="$DR_FROM_VERSION" \
    DR_TO_VERSION="$DR_TO_VERSION" DR_MUTATION="${DR_MUTATION:-none}" \
    dbus-run-session -- bash "$DR_SELF" session || status=$?
  (( status != 124 && status != 137 )) ||
    printf 'session\tFAIL deadline %s reached\n' "$DR_DEADLINE" >> "/home/$USER_NAME/evidence/default-renderer.tsv"
  cp -a "/home/$USER_NAME/evidence/." /out/
  finish_root "$owner" "$status"
}

# Extract one leg's archive after re-checking it against that release's SHA256SUMS.
dr_stage() {
  local leg="$1" archive="$2"
  (cd "/dr/$leg" && sha256sum --check --strict --ignore-missing SHA256SUMS) > "$EVIDENCE/sha256-check-$leg.txt"
  grep -qx "$archive: OK" "$EVIDENCE/sha256-check-$leg.txt"
  mkdir -p "$HOME/stage-$leg"
  tar -xzf "/dr/$leg/$archive" -C "$HOME/stage-$leg"
  record "${leg}_archive" "$archive $(sha256sum "/dr/$leg/$archive" | cut -d' ' -f1)"
  record "${leg}_payload" "version=$(cat "$HOME/stage-$leg/agterm-stage/share/agterm/VERSION")" \
    "commit=$(cat "$HOME/stage-$leg/agterm-stage/share/agterm/COMMIT")"
  record "${leg}_installer" "commit=$(git -C "/dr/installer-$leg" rev-parse HEAD)" \
    "sha256=$(sha256sum "/dr/installer-$leg/scripts/install-linux.sh" | cut -d' ' -f1)"
}

dr_session() {
  EVIDENCE="$HOME/evidence"
  # record() in gates.sh writes gates.tsv; these rows are a separate file the gate verdict never reads.
  record() { local name="$1"; shift; printf '%s\t%s\n' "$name" "$*" >> "$EVIDENCE/default-renderer.tsv"; }
  git config --global --add safe.directory '*'
  # Hyprland keeps its own state under XDG_DATA_HOME (~/.local/share by default), which is inside the
  # tested prefix; keep it out so the empty-prefix check judges only what agterm leaves.
  export XDG_DATA_HOME="$HOME/.hyprland-data"
  start_hyprland
  unset XDG_DATA_HOME
  record mesa "$(pacman -Q mesa | cut -d' ' -f2)"
  record egl_gtk_configs "$(awk -F'\t' '$1 == "gtk-rgba8-window" { print $2 }' "$EVIDENCE/egl-choose-config.txt")"
  dr_stage from "$DR_FROM_ARCHIVE"
  dr_stage to "$DR_TO_ARCHIVE"
  local status=0
  python3 -I /repo/.github/omarchy-x86-native/default-renderer.py run --evidence "$EVIDENCE" \
    --from-stage "$HOME/stage-from/agterm-stage" --to-stage "$HOME/stage-to/agterm-stage" \
    --from-installer /dr/installer-from/scripts/install-linux.sh --to-installer /dr/installer-to/scripts/install-linux.sh \
    --from-version "$DR_FROM_VERSION" --to-version "$DR_TO_VERSION" --mutation "${DR_MUTATION:-none}" \
    > "$EVIDENCE/default-renderer.log" 2>&1 || status=$?
  cp "$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/hyprland.log" "$EVIDENCE/hyprland-runtime.log" 2>/dev/null || true
  stop_hyprland
  cat "$EVIDENCE/default-renderer.tsv"
  return "$status"
}

case "$DR_PHASE" in
  root) dr_root ;;
  setup) setup_phase ;;
  session) dr_session ;;
  *) echo "unknown phase: $DR_PHASE" >&2; exit 2 ;;
esac
