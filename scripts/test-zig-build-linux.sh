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
  if [[ "$(wc -l < "$TEST_ZIG_WORK/git-fetches")" -le "${TEST_ZIG_GIT_FAILURES:-0}" ]]; then
    [[ -z "${TEST_ZIG_GIT_DIAGNOSTIC:-}" ]] || echo "$TEST_ZIG_GIT_DIAGNOSTIC" >&2
    exit 1
  fi
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
  unrelated-git)
    echo 'build.zig:27:20: error: expected type, found error.NoAddressReturned'
    echo '    .url = "git+https://codeberg.org/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b",'
    exit 23 ;;
  git*)
    if [[ -e "$TEST_ZIG_WORK/fetched" ]]; then
      case "$TEST_ZIG_CASE" in
        git-hash) echo 'error: hash mismatch'; exit 19 ;;
        git-repeat) ;;
        *) exit 0 ;;
      esac
    fi
    echo "zig-pkg/translate_c/build.zig.zon:12:20: error: unable to discover remote git server capabilities: ${TEST_ZIG_GIT_ERROR:-NoAddressReturned}"
    if [[ "$TEST_ZIG_CASE" != git-missing-url ]]; then
      echo "    .url = \"${TEST_ZIG_GIT_URL:-git+https://codeberg.org/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b}\","
    fi
    exit 17 ;;
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
[[ "${TEST_ZIG_CURL_FAILURE:-0}" == 0 ]] || exit 22
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
# Transitive Git dependency diagnostics use the same exact-commit archive fallback.
run_case git 0
grep -q 'https://codeberg.org/ziglang/arocc/archive/d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b.tar.gz$' "$WORK/curl-args"
grep -q -- ' --max-redirs 0 ' "$WORK/curl-args" || { echo 'Codeberg archive must reject redirects' >&2; exit 1; }
TEST_ZIG_GIT_ERROR=NetworkUnreachable run_case git 0
run_case git-missing-url 17
[[ ! -e "$WORK/curl-args" ]]
run_case unrelated-git 23
[[ ! -e "$WORK/curl-args" ]]
run_case git-repeat 17
[[ "$(wc -l < "$WORK/curl-args")" -eq 1 ]]
run_case git-hash 19
[[ "$(wc -l < "$WORK/curl-args")" -eq 1 ]]
TEST_ZIG_CURL_FAILURE=1 run_case git 22
[[ ! -e "$WORK/fetched" ]]
for url in \
  'git+https://codeberg.org/ziglang/arocc#d0c8c4d' \
  'git+https://codeberg.org/ziglang/arocc#main' \
  'git+https://example.test/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b' \
  'git+https://codeberg.org/other/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b' \
  'git+https://codeberg.org/ziglang/other-package#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b' \
  'git+https://example.test/git+https://codeberg.org/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b' \
  'git+https://user@codeberg.org/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b' \
  'git+https://codeberg.org/ziglang/arocc?other=main#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b'; do
  TEST_ZIG_GIT_URL="$url" run_case git 17
  [[ ! -e "$WORK/curl-args" ]]
done
TEST_ZIG_GIT_ERROR=UnexpectedResponse run_case git 17
[[ ! -e "$WORK/curl-args" ]]
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
sed 's/#cfac21f"/#main"/' "$WORK/git-project/build.zig.zon" > "$WORK/git-project/unpinned.zon"
mv "$WORK/git-project/unpinned.zon" "$WORK/git-project/build.zig.zon"
rm -f "$WORK/curl-args"
TEST_ZIG_GIT_DIAGNOSTIC='error: unable to discover remote git server capabilities: NoAddressReturned' git_case 4 1 4
[[ ! -e "$WORK/curl-args" ]]
# The direct dependency path uses the full commit, not its mutable ref hint.
sed 's/#main"/#cfac21f3199b2b02ce3fd7db652aa456421fe4ab"/' \
  "$WORK/git-project/build.zig.zon" > "$WORK/git-project/full.zon"
mv "$WORK/git-project/full.zon" "$WORK/git-project/build.zig.zon"
rm -f "$WORK/fetched" "$WORK/curl-args"
TEST_ZIG_GIT_DIAGNOSTIC='error: unable to discover remote git server capabilities: NoAddressReturned' git_case 4 0 1
grep -q 'https://codeberg.org/ziglang/translate-c/archive/cfac21f3199b2b02ce3fd7db652aa456421fe4ab.tar.gz$' "$WORK/curl-args"
[[ -e "$WORK/fetched" ]]
rm -f "$WORK/fetched" "$WORK/curl-args"
TEST_ZIG_CURL_FAILURE=1 TEST_ZIG_GIT_DIAGNOSTIC='error: unable to discover remote git server capabilities: NoAddressReturned' git_case 4 22 1
[[ ! -e "$WORK/fetched" ]]
rm -f "$WORK/curl-args"
TEST_ZIG_GIT_DIAGNOSTIC='error: hash mismatch' git_case 4 1 4
[[ ! -e "$WORK/curl-args" ]]
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
# Real Zig also has to consume the package through a git+https manifest unchanged. Positive cases use
# ordinary builds, which expand the global fetch cache. For the negative case, expand only the altered
# package under its actual computed hash into an isolated --system directory; network fetching and
# previously cached good packages then cannot mask the mismatch. Require this fixture eagerly because
# Zig 0.17.0 can panic while configuring a missing lazy dependency in --system mode.
mkdir -p "$WORK/tampered/fixture-1.0"
cp -R "$WORK/fixture/fixture-1.0/." "$WORK/tampered/fixture-1.0/"
printf 'modified fixture\n' > "$WORK/tampered/fixture-1.0/README"
tar -czf "$WORK/tampered.tar.gz" -C "$WORK/tampered" fixture-1.0
cat > "$WORK/real-bin/zig" <<'ZIG'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == fetch && "$2" == git+https://* ]]; then
  echo 'error: unable to discover remote git server capabilities: NoAddressReturned' >&2
  exit 1
fi
if [[ "$1" == fetch && "$TEST_REAL_MODE" == tampered ]]; then
  hash="$("$TEST_REAL_ZIG" "$@")"
  printf '%s\n' "$hash"
  printf '%s\n' "$hash" > "$PWD/actual-hash"
  mkdir -p "$PWD/offline-pkg/$hash"
  tar -xzf "$ZIG_GLOBAL_CACHE_DIR/p/$hash.tar.gz" -C "$PWD/offline-pkg/$hash" --no-same-owner
  exit 0
fi
if [[ "$1" == build && "$TEST_REAL_MODE" == transitive && ! -e "$PWD/injected" ]]; then
  touch "$PWD/injected"
  echo 'zig-pkg/parent/build.zig.zon:12:20: error: unable to discover remote git server capabilities: NetworkUnreachable'
  echo '    .url = "git+https://codeberg.org/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b",'
  exit 1
fi
exec "$TEST_REAL_ZIG" "$@"
ZIG
for mode in direct transitive tampered; do
  # Zig 0.17 stores fetched package archives in global-cache/p. Clear only this disposable package
  # cache, preserving expensive compiler caches; an earlier correct fixture must not mask a bad fetch.
  rm -rf -- "$WORK/real-cache/p"
  [[ ! -e "$WORK/real-cache/p" ]]
  project="$WORK/real-$mode"
  mkdir "$project"
  cp "$WORK/real/build.zig" "$project/build.zig"
  if [[ "$mode" == transitive ]]; then
    sed 's/\.lazy = true/.lazy = false/' "$WORK/real/build.zig.zon" > "$project/build.zig.zon"
  else
    sed -e 's/\.lazy = true/.lazy = false/' \
      -e 's|https://deps.example.invalid/fixture-1.0.tar.gz|git+https://codeberg.org/ziglang/arocc#d0c8c4d9c55daa7ef6e40cf0f630a5b5e900989b|' \
      "$WORK/real/build.zig.zon" > "$project/build.zig.zon"
  fi
  cp "$project/build.zig.zon" "$project/expected.zon"
  archive="$WORK/fixture-1.0.tar.gz"
  [[ "$mode" != tampered ]] || archive="$WORK/tampered.tar.gz"
  build_args=()
  [[ "$mode" != tampered ]] || build_args=(--system "$project/offline-pkg")
  real=0
  (cd "$project" && TEST_REAL_MODE="$mode" TEST_ZIG_ARCHIVE="$archive" TEST_REAL_ZIG="$REAL_ZIG" \
    ZIG="$WORK/real-bin/zig" ZIG_GLOBAL_CACHE_DIR="$WORK/real-cache" ZIG_LOCAL_CACHE_DIR="$project/.zig-cache" \
    "$ROOT/scripts/zig-build-linux.sh" "${build_args[@]}") > "$WORK/result" 2>&1 || real=$?
  cmp "$project/build.zig.zon" "$project/expected.zon"
  if [[ "$mode" == tampered ]]; then
    if [[ $real -eq 0 ]] || ! grep -q 'error: package not found at.*N-V-__8AAD8AAAARyqkdJydtuyQa7SsnpmSgi4gE551nbr9S' "$WORK/result"; then
      cat "$WORK/result" >&2
      echo 'altered archive unexpectedly satisfied the manifest hash' >&2
      exit 1
    fi
    actual_hash="$(cat "$project/actual-hash")"
    [[ "$actual_hash" != N-V-__8AAD8AAAARyqkdJydtuyQa7SsnpmSgi4gE551nbr9S ]]
    [[ ! -e "$project/offline-pkg/N-V-__8AAD8AAAARyqkdJydtuyQa7SsnpmSgi4gE551nbr9S" ]]
    if [[ "$(ls -A "$WORK/real-cache/p")" != "$actual_hash.tar.gz" ]]; then
      ls -A "$WORK/real-cache/p" >&2
      echo "tampered global cache: expected only $actual_hash.tar.gz" >&2
      exit 1
    fi
  elif [[ $real -ne 0 ]] || ! grep -qx 'fixture dependency resolved' "$WORK/result"; then
    cat "$WORK/result" >&2
    echo "real Zig $mode fallback failed with exit $real" >&2
    exit 1
  else
    [[ -f "$WORK/real-cache/p/N-V-__8AAD8AAAARyqkdJydtuyQa7SsnpmSgi4gE551nbr9S.tar.gz" ]]
  fi
done
echo 'Zig transport fallback passes: TLS/reset and pinned Codeberg routing, compiler-error rejection, multiple dependencies, retry bounds, temporary-directory policy, real direct/transitive package resolution, and rejection of altered archive content against the unchanged manifest hash.'
