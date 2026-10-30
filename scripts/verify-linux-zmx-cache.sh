#!/usr/bin/env bash
# Verify that the pinned zmx runtime and its license are staged for Linux packaging.
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: scripts/verify-linux-zmx-cache.sh ZMX_DIRECTORY" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$1"
[[ "$VENDOR" = /* ]] || VENDOR="$ROOT/$VENDOR"
# shellcheck source=../linux/zmx.env
source "$ROOT/linux/zmx.env"
# shellcheck source=../linux/arch.sh
source "$ROOT/linux/arch.sh"
ZMX_BUILD_POLICY="target=$ZIG_TARGET cpu=$ZMX_CPU"
ZMX_PATCH_DIGEST="$(cat "$ROOT/scripts/zmx-patches"/*.patch "$ROOT/scripts/zmx-patches/ghostty"/*.patch | sha256sum | cut -c1-16)"
ZMX_STAMP="$ZMX_REV $ZMX_PATCH_DIGEST"

[[ -x "$VENDOR/zmx" ]] || { echo "missing executable zmx: $VENDOR/zmx" >&2; exit 1; }
[[ -s "$VENDOR/LICENSE" ]] || { echo "missing zmx license: $VENDOR/LICENSE" >&2; exit 1; }
[[ -s "$VENDOR/REVISION" ]] || { echo "missing zmx revision: $VENDOR/REVISION" >&2; exit 1; }
[[ "$(cat "$VENDOR/REVISION")" == "$ZMX_STAMP" ]] || {
  echo "zmx cache revision does not match $ZMX_STAMP" >&2
  exit 1
}
# Validate the distribution policy before executing a potentially host-native cached helper.
[[ -s "$VENDOR/BUILD_POLICY" ]] || { echo "missing zmx build policy: $VENDOR/BUILD_POLICY" >&2; exit 1; }
[[ "$(cat "$VENDOR/BUILD_POLICY")" == "$ZMX_BUILD_POLICY" ]] || {
  echo "zmx cache build policy does not match $ZMX_BUILD_POLICY" >&2
  exit 1
}
"$ROOT/scripts/verify-linux-architecture.sh" "$VENDOR/zmx"
CHECK_DIR="$(mktemp -d)"
trap 'rm -rf "$CHECK_DIR"' EXIT
ZMX_DIR="$CHECK_DIR" "$VENDOR/zmx" help >/dev/null
echo "→ verified pinned zmx cache at $VENDOR"
