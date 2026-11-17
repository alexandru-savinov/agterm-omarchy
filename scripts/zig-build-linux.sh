#!/usr/bin/env bash
# Retain Zig package hashes while falling back to curl for recognized dependency transport failures.
set -euo pipefail
ZIG="${ZIG:-zig}"
WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/agterm-zig-fetch.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
seen_urls=()

# A git+https dependency (zmx's translate_c, on codeberg) has no archive for the curl fallback below, so it
# is fetched into the cache first, with retries. The manifest's hash still decides which cache entry the
# build uses, so a fetch that hashes differently is never built.
if [[ -f build.zig.zon ]]; then
  while IFS= read -r url; do
    for ((try = 1; ; try++)); do
      "$ZIG" fetch "$url" > /dev/null && break
      (( try < 4 )) || { echo "Zig git dependency fetch failed $try times: $url" >&2; exit 1; }
      echo "Zig git dependency fetch failed; retrying: $url" >&2
      sleep $(( try * ${ZIG_FETCH_RETRY_SECONDS:-5} ))
    done
  done < <(sed -n 's/.*\.url = "\(git+https:\/\/[^" ]*\)".*/\1/p' build.zig.zon)
fi

for ((attempt = 0; attempt < 8; attempt++)); do
  if "$ZIG" build "$@" 2>&1 | tee "$WORK/build.log"; then
    exit 0
  else
    status=$?
  fi
  # Match the observed Zig HTTP-read diagnostic, not arbitrary compiler errors mentioning a reset.
  grep -Eq 'TlsInitializationFailed|: error: HTTP response read failure: ConnectionResetByPeer$' \
    "$WORK/build.log" || exit "$status"
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
    echo "Zig dependency transport failed; fetching with certificate-verified curl: $url" >&2
    curl --fail --location --proto '=https' --proto-redir '=https' \
      --show-error --silent --retry 3 --connect-timeout 20 --max-time 300 \
      --output "$archive" "$url"
    # Zig computes the content hash; the unchanged manifest selects only the expected cache entry.
    "$ZIG" fetch "$archive"
  done
done
echo "too many Zig dependency transport failures" >&2
exit 1
