#!/usr/bin/env bash
# Retain Zig package hashes while falling back to curl for unsupported TLS handshakes.
set -euo pipefail
ZIG="${ZIG:-zig}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
seen_urls=()

for ((attempt = 0; attempt < 8; attempt++)); do
  if "$ZIG" build "$@" 2>&1 | tee "$WORK/build.log"; then
    exit 0
  else
    status=$?
  fi
  grep -q 'TlsInitializationFailed' "$WORK/build.log" || exit "$status"
  url="$(sed -n 's/.*\.url = "\(https:\/\/[^" ]*\)".*/\1/p' "$WORK/build.log" | head -1)"
  [[ -n "$url" ]] || exit "$status"
  for prior in "${seen_urls[@]}"; do
    [[ "$prior" != "$url" ]] || exit "$status"
  done
  seen_urls+=("$url")
  archive="$WORK/$(basename "${url%%\?*}")"
  echo "Zig TLS failed; fetching dependency with certificate-verified curl: $url" >&2
  curl --fail --location --show-error --silent --retry 3 --connect-timeout 20 --max-time 300 \
    --output "$archive" "$url"
  # Zig computes the content hash; the unchanged manifest selects only the expected cache entry.
  "$ZIG" fetch "$archive"
done
echo "too many Zig TLS dependency failures" >&2
exit 1
