#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
REAL_ZIG="${GHOSTTY_ZIG:-}"
if [[ ! -x "$REAL_ZIG" ]] && command -v mise >/dev/null; then
  REAL_ZIG="$(mise where zig@0.17.0 2>/dev/null || true)/bin/zig"
fi
[[ -x "$REAL_ZIG" ]] || REAL_ZIG="$(command -v zig || true)"
[[ -x "$REAL_ZIG" && "$($REAL_ZIG version)" == 0.17.0 ]] || {
  echo "zig 0.17.0 is required for the real-archive fallback case (set GHOSTTY_ZIG)" >&2; exit 1;
}
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin"
export TEST_ZIG_WORK="$WORK"

# A dependency archive with one top-level directory, as most published tarballs have, and one without.
mkdir -p "$WORK/fixture/fixture-1.0/src"
printf 'fixture\n' > "$WORK/fixture/fixture-1.0/README"
printf 'int fixture(void) { return 1; }\n' > "$WORK/fixture/fixture-1.0/src/fixture.c"
printf '#!/bin/sh\necho fixture\n' > "$WORK/fixture/fixture-1.0/run.sh"
chmod 755 "$WORK/fixture/fixture-1.0/run.sh"
ln -s README "$WORK/fixture/fixture-1.0/LINK"
# macOS tar would otherwise add AppleDouble members, which change the package hash.
export COPYFILE_DISABLE=1
tar -czf "$WORK/fixture-1.0.tar.gz" -C "$WORK/fixture" fixture-1.0
tar -czf "$WORK/flat.tar.gz" -C "$WORK/fixture/fixture-1.0" README src
export TEST_ZIG_ARCHIVE="$WORK/fixture-1.0.tar.gz"
cat > "$WORK/bin/zig" <<'ZIG'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == fetch && "$2" == git+https://* ]]; then
  echo "$2" >> "$TEST_ZIG_WORK/git-fetches"
  [[ "$(wc -l < "$TEST_ZIG_WORK/git-fetches")" -gt "${TEST_ZIG_GIT_FAILURES:-0}" ]] || exit 1
  echo computed-hash
  exit 0
fi
if [[ "$1" == fetch ]]; then
  [[ -d "$2" ]]
  printf '%s\n' "$2" > "$TEST_ZIG_WORK/fetched"
  echo computed-hash
  exit 0
fi
[[ "$1" == build && "$2" == -Doptimize=ReleaseFast ]]
case "$TEST_ZIG_CASE" in
  ordinary) echo 'compile error'; exit 23 ;;
  unrelated-reset)
    echo 'build.zig:27:20: error: expected type, found error.ConnectionResetByPeer'
    echo '    .url = "https://example.test/package.tar.gz",'
    exit 23 ;;
  success) exit 0 ;;
  tls|multi|reset)
    [[ ! -e "$TEST_ZIG_WORK/fetched" ]] || exit 0 ;;
esac
if [[ "$TEST_ZIG_CASE" == reset* ]]; then
  # Exact diagnostic and manifest shape from failed ARM64 job 123719308427.
  echo '/var/tmp/agterm-linux-build.fixture/ghostty/build.zig.zon:27:20: error: HTTP response read failure: ConnectionResetByPeer'
  if [[ "$TEST_ZIG_CASE" != reset-missing-url ]]; then
    echo '            .url = "https://deps.files.ghostty.org/vaxis-1dbbe575dff4586fe51e3217aa5c3fecdcbb6089.tar.gz",'
    echo '                   ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
  fi
  exit 1
fi
echo 'error: unable to connect to server: TlsInitializationFailed'
if [[ "$TEST_ZIG_CASE" != missing-url ]]; then
  echo '    .url = "https://example.test/package.tar.gz",'
fi
if [[ "$TEST_ZIG_CASE" == multi ]]; then
  echo '    .url = "https://example.test/second.tar.gz",'
fi
exit 17
ZIG
cat > "$WORK/bin/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_ZIG_WORK/curl-args"
while [[ "$1" != --output ]]; do
  [[ "$1" != --insecure && "$1" != -k ]]
  shift
done
printf '%s\n' "$2" > "$TEST_ZIG_WORK/archive-path"
cp "$TEST_ZIG_ARCHIVE" "$2"
CURL
chmod +x "$WORK/bin/zig" "$WORK/bin/curl"
export PATH="$WORK/bin:$PATH"
export ZIG="$WORK/bin/zig"

run_case() {
  rm -f "$WORK/fetched" "$WORK/curl-args"
  local actual=0
  TEST_ZIG_CASE="$1" "$ROOT/scripts/zig-build-linux.sh" -Doptimize=ReleaseFast \
    > "$WORK/result" 2>&1 || actual=$?
  [[ "$actual" == "$2" ]] || {
    cat "$WORK/result" >&2
    echo "$1: expected exit $2, got $actual" >&2
    exit 1
  }
}

run_case success 0
[[ ! -e "$WORK/curl-args" ]]
run_case ordinary 23
[[ ! -e "$WORK/curl-args" ]]
run_case unrelated-reset 23
[[ ! -e "$WORK/curl-args" ]]
run_case reset-missing-url 1
[[ ! -e "$WORK/curl-args" ]]
run_case reset 0
[[ -e "$WORK/fetched" && "$(wc -l < "$WORK/curl-args")" -eq 1 ]]
[[ "$(cat "$WORK/fetched")" == */package.*/fixture-1.0 ]] || { cat "$WORK/fetched" >&2; exit 1; }
TEST_ZIG_ARCHIVE="$WORK/flat.tar.gz" run_case reset 0
[[ "$(basename "$(cat "$WORK/fetched")")" == package.* ]] || { cat "$WORK/fetched" >&2; exit 1; }
grep -q -- '--proto =https --proto-redir =https' "$WORK/curl-args"
run_case reset-repeat 1
[[ "$(wc -l < "$WORK/curl-args")" -eq 1 ]]
run_case missing-url 17
[[ ! -e "$WORK/curl-args" ]]
run_case tls 0
[[ -e "$WORK/fetched" && "$(wc -l < "$WORK/curl-args")" -eq 1 ]]
grep -q -- '--proto =https --proto-redir =https' "$WORK/curl-args"
run_case multi 0
[[ "$(wc -l < "$WORK/curl-args")" -eq 2 ]]
run_case repeat 17
[[ "$(wc -l < "$WORK/curl-args")" -eq 1 ]]
mkdir "$WORK/custom-temp"
TMPDIR="$WORK/custom-temp" run_case tls 0
[[ "$(cat "$WORK/archive-path")" == "$WORK/custom-temp/"* ]]
[[ -z "$(ls -A "$WORK/custom-temp")" ]]
# git+https dependencies are prefetched with retries before the build, which still runs once.
mkdir "$WORK/git-project"
printf '.{\n    .translate_c = .{\n        .url = "git+https://codeberg.org/ziglang/translate-c?ref=zig-0.17.x#cfac21f",\n    },\n}\n' \
  > "$WORK/git-project/build.zig.zon"
git_case() {
  rm -f "$WORK/git-fetches"
  local actual=0
  (cd "$WORK/git-project" && TEST_ZIG_CASE=success TEST_ZIG_GIT_FAILURES="$1" ZIG_FETCH_RETRY_SECONDS=0 \
    "$ROOT/scripts/zig-build-linux.sh" -Doptimize=ReleaseFast) > "$WORK/result" 2>&1 || actual=$?
  [[ "$actual" == "$2" ]] || { cat "$WORK/result" >&2; echo "git prefetch after $1 failures: expected $2, got $actual" >&2; exit 1; }
  [[ "$(wc -l < "$WORK/git-fetches")" -eq "$3" ]]
}
git_case 0 0 1
git_case 2 0 3
git_case 4 1 4
grep -qx 'git+https://codeberg.org/ziglang/translate-c?ref=zig-0.17.x#cfac21f' "$WORK/git-fetches"
if [[ "$(uname -s)" == Linux ]]; then
  unset TMPDIR
  run_case tls 0
  [[ "$(cat "$WORK/archive-path")" == /var/tmp/agterm-zig-fetch.* ]]
fi

# Real Zig: the fallback's cache entry must satisfy the hash the dependent manifest declares, which the build
# checks. The hash is what `zig build` computed fetching this archive over HTTP.
mkdir "$WORK/real" "$WORK/real-bin"
cat > "$WORK/real/build.zig.zon" <<'ZON'
.{
    .name = .fallback_probe,
    .version = "0.0.0",
    .fingerprint = 0xa42c470db65e1553,
    .minimum_zig_version = "0.17.0",
    .dependencies = .{
        .fixture = .{
            .url = "https://deps.example.invalid/fixture-1.0.tar.gz",
            .hash = "N-V-__8AAD8AAAARyqkdJydtuyQa7SsnpmSgi4gE551nbr9S",
            .lazy = true,
        },
    },
    .paths = .{""},
}
ZON
cat > "$WORK/real/build.zig" <<'BUILD'
const std = @import("std");
pub fn build(b: *std.Build) void {
    if (b.lazyDependency("fixture", .{}) != null) std.debug.print("fixture dependency resolved\n", .{});
}
BUILD
cat > "$WORK/real-bin/zig" <<'ZIG'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == build && ! -e "$TEST_ZIG_WORK/real-reset" ]]; then
  touch "$TEST_ZIG_WORK/real-reset"
  echo "$PWD/build.zig.zon:9:20: error: HTTP response read failure: ConnectionResetByPeer"
  echo '            .url = "https://deps.example.invalid/fixture-1.0.tar.gz",'
  exit 1
fi
exec "$TEST_REAL_ZIG" "$@"
ZIG
chmod +x "$WORK/real-bin/zig"
real=0
(cd "$WORK/real" && TEST_REAL_ZIG="$REAL_ZIG" ZIG="$WORK/real-bin/zig" ZIG_GLOBAL_CACHE_DIR="$WORK/real-cache" \
  ZIG_LOCAL_CACHE_DIR="$WORK/real/.zig-cache" "$ROOT/scripts/zig-build-linux.sh") > "$WORK/result" 2>&1 || real=$?
if [[ $real -ne 0 || ! -e "$WORK/real-reset" ]] \
  || ! grep -qx 'N-V-__8AAD8AAAARyqkdJydtuyQa7SsnpmSgi4gE551nbr9S' "$WORK/result" \
  || ! grep -qx 'fixture dependency resolved' "$WORK/result"; then
  cat "$WORK/result" >&2
  echo "real Zig fallback: exit $real, or the cached package missed the manifest hash" >&2
  exit 1
fi
echo 'Zig transport fallback passes: TLS/reset routing, compiler-error rejection, multiple dependencies, retry bounds, git+https prefetch, temporary-directory policy, and a real archive that the build accepts by its manifest hash.'
