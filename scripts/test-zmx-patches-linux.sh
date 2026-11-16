#!/usr/bin/env bash
# Run upstream zmx's own gates (zig fmt, zig build test, bats) over the pinned zmx with scripts/zmx-patches.
set -euo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
# shellcheck source=../linux/zmx.env
source "$ROOT/linux/zmx.env"
GHOSTTY_REPO="https://github.com/ghostty-org/ghostty"
# bats-core v1.11.1
BATS_REPO="https://github.com/bats-core/bats-core"
BATS_REV="b640ec3cf2c7c9cfc9e6351479261186f76eeec8"

ZIG="${ZMX_ZIG:-${GHOSTTY_ZIG:-}}"
if [[ ! -x "$ZIG" ]] && command -v mise >/dev/null; then
  ZIG="$(mise where "zig@$ZMX_ZIG_VERSION" 2>/dev/null || true)/bin/zig"
fi
[[ -x "$ZIG" ]] || ZIG="$(command -v zig || true)"
[[ -x "$ZIG" && "$($ZIG version)" == "$ZMX_ZIG_VERSION" ]] || {
  echo "zig $ZMX_ZIG_VERSION is required for zmx (set ZMX_ZIG)" >&2; exit 1;
}

WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/agterm-zmx-tests.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
fetch() {
  local url="$1" rev="$2" directory="$3"
  git init -q "$directory"
  git -C "$directory" remote add origin "$url"
  git -C "$directory" fetch -q --depth 1 origin "$rev"
  git -C "$directory" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [[ "$(git -C "$directory" rev-parse HEAD)" == "$rev" ]] || { echo "$url is not at $rev" >&2; exit 1; }
}
# 0002 points zmx at ../zmx-ghostty, a sibling of the zmx tree.
fetch "$ZMX_REPO" "$ZMX_REV" "$WORK/zmx"
fetch "$GHOSTTY_REPO" "$ZMX_GHOSTTY_PIN" "$WORK/zmx-ghostty"
fetch "$BATS_REPO" "$BATS_REV" "$WORK/bats"
shopt -s nullglob
for patch in "$ROOT/scripts/zmx-patches/ghostty"/*.patch; do git -C "$WORK/zmx-ghostty" apply "$patch"; done
for patch in "$ROOT/scripts/zmx-patches"/*.patch; do git -C "$WORK/zmx" apply "$patch"; done
shopt -u nullglob

cd "$WORK/zmx"
export ZIG_GLOBAL_CACHE_DIR="$WORK/zig-global-cache" ZIG_LOCAL_CACHE_DIR="$WORK/zmx/.zig-cache"
export XDG_CACHE_HOME="$WORK/xdg-cache"
"$ZIG" fmt --check .
ZIG="$ZIG" "$ROOT/scripts/zig-build-linux.sh" test --summary all
ZIG="$ZIG" "$ROOT/scripts/zig-build-linux.sh"
# The suite builds zmx itself when this binary is missing; detached stdin keeps `zmx run` from waiting on it.
ZMX="$WORK/zmx/zig-out/bin/zmx" "$WORK/bats/bin/bats" --jobs 1 test/*.bats < /dev/null
