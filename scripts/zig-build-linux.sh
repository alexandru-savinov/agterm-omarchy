#!/usr/bin/env bash
# Retain Zig package hashes while falling back to curl for unsupported TLS handshakes.
set -euo pipefail
ZIG="${ZIG:-zig}"
WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/agterm-zig-fetch.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
seen_urls=()

for ((attempt = 0; attempt < 8; attempt++)); do
  if "$ZIG" build "$@" 2>&1 | tee "$WORK/build.log"; then
    exit 0
  else
    status=$?
  fi
  grep -q 'TlsInitializationFailed' "$WORK/build.log" || exit "$status"
  urls=()
  while IFS= read -r url; do urls+=("$url"); done < <(
    sed -n 's/.*\.url = "\(https:\/\/[^" ]*\)".*/\1/p' "$WORK/build.log" | sort -u
  )
  [[ ${#urls[@]} -gt 0 ]] || exit "$status"
  for url in "${urls[@]}"; do
    for prior in "${seen_urls[@]}"; do
      [[ "$prior" != "$url" ]] || exit "$status"
    done
    seen_urls+=("$url")
    archive="$WORK/$(basename "${url%%\?*}")"
    echo "Zig TLS failed; fetching dependency with certificate-verified curl: $url" >&2
    curl --fail --location --proto '=https' --proto-redir '=https' \
      --show-error --silent --retry 3 --connect-timeout 20 --max-time 300 \
      --output "$archive" "$url"
    # Zig computes the content hash; the unchanged manifest selects only the expected cache entry.
    "$ZIG" fetch "$archive"
  done
done
echo "too many Zig TLS dependency failures" >&2
exit 1
