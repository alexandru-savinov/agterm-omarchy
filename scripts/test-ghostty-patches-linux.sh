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

# Track the source file as well as the test name so another module cannot satisfy the check.
# src/App.zig and every Zig file the patches under scripts/ghostty-patches touch must stay zig-fmt clean;
# the linux/patches adapters predate the check and one of them is not (src/apprt/embedded.zig).
formatted=(src/App.zig)
tests="$WORK/tests.tsv"
for ghostty_patch in "${GHOSTTY_PATCHES[@]}"; do
  git -C "$SOURCE" apply "$ghostty_patch"
  if [[ "$ghostty_patch" == "$ROOT/scripts/ghostty-patches/"* ]]; then
    while IFS= read -r file; do formatted+=("$file"); done < <(
      awk '/^\+\+\+ / { file = $2; sub(/^[^\/]*\//, "", file); if (file ~ /\.zig$/) print file }' "$ghostty_patch"
    )
  fi
  awk '/^\+\+\+ / { file = $2; sub(/^[^\/]*\//, "", file) }
    file ~ /\.zig$/ && /^\+test "/ {
      name = $0; sub(/^\+test "/, "", name); sub(/" \{$/, "", name); print file "\t" name }' "$ghostty_patch" >> "$tests"
done
[[ -s "$tests" ]] || { echo "no Zig tests in the patch chain" >&2; exit 1; }
sort -u -o "$tests" "$tests"

# Register test files explicitly, including runtimes omitted by -Dapp-runtime=none.
{
  printf '\ntest {\n'
  while IFS= read -r file; do
    [[ "$file" == src/* && -f "$SOURCE/$file" ]] || { echo "patch test source outside Ghostty test root: $file" >&2; exit 1; }
    [[ "$file" == src/main.zig ]] || printf '    _ = @import("%s");\n' "${file#src/}"
  done < <(cut -f1 "$tests" | uniq)
  printf '}\n'
} >> "$SOURCE/src/main.zig"

names=()
filters=()
while IFS=$'\t' read -r file name; do
  module="${file#src/}"
  module="${module%.zig}"
  test_id="${module//\//.}.test.$name"
  names+=("$test_id")
  filters+=("-Dtest-filter=$test_id")
done < "$tests"

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
  [[ "$(grep -cF -- " $name...OK" "$WORK/tests.log")" == 1 ]] || missing+=("$name")
done
[[ $status -eq 0 && ${#missing[@]} -eq 0 && "$(tail -n 1 "$WORK/tests.log")" =~ ^All\ [0-9]+\ tests\ passed\.$ ]] || {
  printf 'patch tests that did not run and pass (%d of %d):\n' "${#missing[@]}" "${#names[@]}" >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 1
}
echo "all ${#names[@]} patch tests ran and passed"

# Temporary hosted verification for #457. Each arm restores the original source.
if [[ "$(uname -m)" == aarch64 ]]; then
  (
    cd "$SOURCE"
    export ZIG_GLOBAL_CACHE_DIR="$WORK/zig-global-cache"
    export ZIG_LOCAL_CACHE_DIR="$SOURCE/.zig-cache"
    export XDG_CACHE_HOME="$WORK/xdg-cache"
    cp src/termio/Exec.zig "$WORK/Exec.zig"
    for arm in gather-call exit-call drain-cap exit-bound; do
      cp "$WORK/Exec.zig" src/termio/Exec.zig
      python3 - "$arm" <<'PY'
from pathlib import Path
import sys

old, new = {
    "gather-call": (
        "if (hungUp(fd, pollfds[0].revents, pipeline)) return;",
        'if (pollfds[0].revents & posix.POLL.HUP != 0) {\n'
        '    log.info("pty fd closed, read thread exiting", .{});\n'
        '    return;\n'
        '}',
    ),
    "exit-call": (
        "execdata.exit_hold.wait(td.loop, termio.Termio.ThreadData, td, pushChildExited);",
        "pushChildExited(td);",
    ),
    "drain-cap": ("while (drained < hangup_drain_max) {", "while (true) {"),
    "exit-bound": (
        "if (done or self.start.untilNow(global.io(), .awake).toMilliseconds() >= max_ms) {",
        "if (done) {",
    ),
}[sys.argv[1]]
path = Path("src/termio/Exec.zig")
text = path.read_text()
assert text.count(old) == 1
path.write_text(text.replace(old, new))
PY
      case "$arm" in
        gather-call)
          test_name='the gather stage drains output when its outer poll reports hangup'
          expected='error.TestExpectedEqual'
          ;;
        exit-call)
          test_name='process exit holds the real surface mailbox notice until the reader finishes'
          expected='error.TestUnexpectedResult'
          ;;
        drain-cap)
          test_name='a hangup drain stops at its cap when the fd never runs dry'
          expected='test deadline exceeded: hangup drain cap (5 s)'
          ;;
        exit-bound)
          test_name='the exit message goes after the bound when the read thread never finishes'
          expected='test deadline exceeded: exit hold bound (5 s)'
          ;;
      esac
      if "$GHOSTTY_ZIG" build test -Dapp-runtime=none "-Dtest-filter=termio.Exec.test.$test_name" > "$WORK/$arm.log" 2>&1; then
        cat "$WORK/$arm.log"
        echo "Unexpected pass: $arm" >&2
        exit 1
      fi
      cat "$WORK/$arm.log"
      grep -F -- "$expected" "$WORK/$arm.log"
      printf 'Verified %s: %s failed with %s\n' "$arm" "$test_name" "$expected"
    done
  )
fi
