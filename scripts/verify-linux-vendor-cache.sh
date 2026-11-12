#!/usr/bin/env bash
# Verify that a vendored libghostty cache is complete, was built from the current pinned sources and
# patches, and belongs to the current Linux host.
# Usage: scripts/verify-linux-vendor-cache.sh VENDOR_DIRECTORY
#        scripts/verify-linux-vendor-cache.sh --print-stamp
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: scripts/verify-linux-vendor-cache.sh VENDOR_DIRECTORY | --print-stamp" >&2
  exit 2
fi

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
STAMP_NAME="SOURCE_STAMP"

# The build stamp names every input that changes the compiled library: the Ghostty revision, each
# Linux patch (applied or not, so a new patch file also invalidates the cache), and the resource pin.
expected_stamp() {
  local env_file="$ROOT/linux/ghostty-resources.env" patch rev
  local patches=()
  [[ -s "$env_file" ]] || { echo "missing Ghostty source pin: $env_file" >&2; return 1; }
  rev="$(sed -n 's/^GHOSTTY_REV="\([0-9a-f]\{40\}\)"$/\1/p' "$env_file")"
  [[ "$rev" =~ ^[0-9a-f]{40}$ ]] || { echo "no 40-hex GHOSTTY_REV in $env_file" >&2; return 1; }
  shopt -s nullglob
  # The shared upstream teardown patches (scripts/ghostty-patches, also applied by the macOS build) and the
  # Linux renderer adapters (linux/patches) both change the compiled library.
  patches=("$ROOT/scripts/ghostty-patches"/*.patch "$ROOT/linux/patches"/*.patch)
  shopt -u nullglob
  (( ${#patches[@]} )) || { echo "no Ghostty patches found in $ROOT/scripts/ghostty-patches or $ROOT/linux/patches" >&2; return 1; }
  printf 'ghostty-rev %s\n' "$rev"
  for patch in "${patches[@]}"; do
    printf 'patch %s %s\n' "$(basename "$patch")" "$(sha256sum < "$patch" | cut -d' ' -f1)"
  done
  printf 'resources %s\n' "$(sha256sum < "$env_file" | cut -d' ' -f1)"
}

if [[ "$1" == --print-stamp ]]; then
  expected_stamp
  exit 0
fi

VENDOR="$1"
[[ "$VENDOR" = /* ]] || VENDOR="$ROOT/$VENDOR"

for required in lib/libghostty.so include/ghostty.h include/ghostty/vt.h; do
  [[ -s "$VENDOR/$required" ]] || {
    echo "missing or empty vendored libghostty file: $VENDOR/$required" >&2
    exit 1
  }
done

EXPECTED="$(expected_stamp)"
[[ -s "$VENDOR/$STAMP_NAME" ]] || {
  echo "libghostty cache has no build stamp ($VENDOR/$STAMP_NAME); rebuild it with scripts/setup-linux.sh" >&2
  exit 1
}
if [[ "$(cat "$VENDOR/$STAMP_NAME")" != "$EXPECTED" ]]; then
  echo "libghostty cache was built from different Ghostty sources or patches; rebuild it with scripts/setup-linux.sh" >&2
  diff <(printf '%s\n' "$EXPECTED") "$VENDOR/$STAMP_NAME" | sed -n 's/^[<>] /  /p' >&2 || true
  exit 1
fi

python3 "$ROOT/scripts/linux-notices.py" verify-ghostty "$VENDOR/share/licenses/ghostty"
"$ROOT/scripts/verify-linux-architecture.sh" "$VENDOR/lib/libghostty.so"
"$ROOT/scripts/verify-linux-resources.sh" "$VENDOR/share"
echo "→ verified current-architecture libghostty cache at $VENDOR"
