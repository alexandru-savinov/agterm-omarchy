#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin"
export TEST_ZIG_WORK="$WORK"
cat > "$WORK/bin/zig" <<'ZIG'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == fetch ]]; then
  [[ -s "$2" ]]
  touch "$TEST_ZIG_WORK/fetched"
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
printf 'archive' > "$2"
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
if [[ "$(uname -s)" == Linux ]]; then
  unset TMPDIR
  run_case tls 0
  [[ "$(cat "$WORK/archive-path")" == /var/tmp/agterm-zig-fetch.* ]]
fi
echo 'Zig transport fallback passes: TLS/reset routing, compiler-error rejection, multiple dependencies, retry bounds, and temporary-directory policy.'
