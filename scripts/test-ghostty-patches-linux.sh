#!/usr/bin/env bash
# Run the Zig tests the Linux patch chain adds to any libghostty Zig source, against the pinned source.
set -euo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
# shellcheck source=../linux/ghostty-build.sh
source "$ROOT/linux/ghostty-build.sh"

GHOSTTY_ZIG="${GHOSTTY_ZIG:-}"
if [[ ! -x "$GHOSTTY_ZIG" ]] && command -v mise >/dev/null; then
  GHOSTTY_ZIG="$(mise where zig@0.17.0 2>/dev/null || true)/bin/zig"
fi
[[ -x "$GHOSTTY_ZIG" ]] || GHOSTTY_ZIG="$(command -v zig || true)"
[[ -x "$GHOSTTY_ZIG" && "$($GHOSTTY_ZIG version)" == 0.17.0 ]] || {
  echo "zig 0.17.0 is required for Ghostty (set GHOSTTY_ZIG)" >&2; exit 1;
}

WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/agterm-ghostty-tests.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
SOURCE="$WORK/ghostty"
git init -q "$SOURCE"
git -C "$SOURCE" remote add origin https://github.com/ghostty-org/ghostty
git -C "$SOURCE" fetch -q --depth 1 origin "$GHOSTTY_REV"
git -C "$SOURCE" -c advice.detachedHead=false checkout -q FETCH_HEAD
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$GHOSTTY_REV" ]]

# Only applied patches contribute tests, so a patch dropped from the chain drops its tests too. Every
# patched Zig file counts, whatever prefix its diff uses (b/, w/), not only src/App.zig (#435), except
# src/apprt/embedded.zig: the -Dapp-runtime=none test build does not compile it, so its tests cannot run
# here and would only fail the check (#435 keeps that open).
names=()
# src/App.zig and every Zig file the patches under scripts/ghostty-patches touch must stay zig-fmt clean;
# the linux/patches adapters predate the check and one of them is not (src/apprt/embedded.zig).
formatted=(src/App.zig)
for ghostty_patch in "${GHOSTTY_PATCHES[@]}"; do
  git -C "$SOURCE" apply "$ghostty_patch"
  if [[ "$ghostty_patch" == "$ROOT/scripts/ghostty-patches/"* ]]; then
    while IFS= read -r file; do formatted+=("$file"); done < <(
      awk '/^\+\+\+ / { file = $2; sub(/^[^\/]*\//, "", file); if (file ~ /\.zig$/) print file }' "$ghostty_patch"
    )
  fi
  while IFS=$'\t' read -r file name; do names+=("$name"); done < <(
    awk '/^\+\+\+ / { file = $2; sub(/^[^\/]*\//, "", file) }
      file ~ /^src\/.*\.zig$/ && file != "src/apprt/embedded.zig" && /^\+test "/ {
        name = $0; sub(/^\+test "/, "", name); sub(/" \{$/, "", name); print file "\t" name }' "$ghostty_patch"
  )
done
[[ ${#names[@]} -gt 0 ]] || { echo "no Zig tests in the patch chain" >&2; exit 1; }
filters=()
for name in "${names[@]}"; do filters+=("-Dtest-filter=$name"); done

mapfile -t formatted < <(printf '%s\n' "${formatted[@]}" | sort -u)
"$GHOSTTY_ZIG" fmt --check "${formatted[@]/#/$SOURCE/}"
(
  cd "$SOURCE"
  export ZIG_GLOBAL_CACHE_DIR="$WORK/zig-global-cache"
  export ZIG_LOCAL_CACHE_DIR="$SOURCE/.zig-cache"
  export XDG_CACHE_HOME="$WORK/xdg-cache"
  ZIG="$GHOSTTY_ZIG" "$ROOT/scripts/zig-build-linux.sh" test -Dapp-runtime=none -Demit-test-exe=true "${filters[@]}"
)

# The build runner's summary names no tests; the installed runner lists each one, so a filter that
# matches nothing or a skipped test fails here. Unnamed reference blocks match every filter and run too.
status=0
"$SOURCE/zig-out/bin/ghostty-test" > "$WORK/tests.log" 2>&1 || status=$?
cat "$WORK/tests.log"
missing=()
for name in "${names[@]}"; do
  [[ "$(grep -cF -- ".test.$name...OK" "$WORK/tests.log")" == 1 ]] || missing+=("$name")
done
[[ $status -eq 0 && ${#missing[@]} -eq 0 && "$(tail -n 1 "$WORK/tests.log")" =~ ^All\ [0-9]+\ tests\ passed\.$ ]] || {
  printf 'patch tests that did not run and pass (%d of %d):\n' "${#missing[@]}" "${#names[@]}" >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 1
}
echo "all ${#names[@]} patch tests ran and passed"
