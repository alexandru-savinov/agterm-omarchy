#!/usr/bin/env bash
# Retain Zig package hashes while falling back to curl for recognized dependency transport failures.
set -euo pipefail
ZIG="${ZIG:-zig}"
WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/agterm-zig-fetch.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
seen_urls=()

# Only the observed Codeberg Git failures qualify, not compiler diagnostics containing an error name.
git_transport_failed() {
  grep -Eq '(^|: )error: unable to discover remote git server capabilities: (NoAddressReturned|NetworkUnreachable)$' "$1"
}

# Use the same origin and immutable full commit. Do not translate branches, short hashes, arbitrary
# hosts, credentials, or query parameters into archive URLs. The ref hint is not the revision.
codeberg_archive() {
  local pattern='^git\+https://codeberg\.org/ziglang/(translate-c|arocc)(\?ref=[A-Za-z0-9._/-]+)?#([0-9a-f]{40})$'
  [[ "$1" =~ $pattern ]] || return 1
  printf 'https://codeberg.org/ziglang/%s/archive/%s.tar.gz\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}"
}

fetch_archive() {
  local original="$1" url="$2" prior archive package
  local curl_args=(--disable --fail --location --proto '=https' --proto-redir '=https')
  local top=()
  for prior in "${seen_urls[@]}"; do
    [[ "$prior" != "$original" ]] || return 1
  done
  seen_urls+=("$original")
  archive="$WORK/archive.${#seen_urls[@]}.tar.gz"
  echo "Zig dependency transport failed; fetching with certificate-verified curl: $url" >&2
  [[ "$original" != git+https://* ]] || curl_args+=(--max-redirs 0)
  curl "${curl_args[@]}" \
    --show-error --silent --retry 3 --connect-timeout 20 --max-time 300 \
    --output "$archive" "$url"
  # Zig 0.17.0's `zig fetch <archive>` caches an archive with the top-level directory its printed hash omits,
  # so the build rejects that entry as a hash mismatch. Fetch the unpacked tree instead, without a sole
  # top-level directory, as Zig's unpacker drops it. Zig computes the content hash; the unchanged manifest
  # selects only the expected cache entry. Never rewrite a manifest or rename a cache entry to force a match.
  package="$(mktemp -d "$WORK/package.XXXXXX")"
  tar -xf "$archive" -C "$package" --no-same-owner
  while IFS= read -r -d '' entry; do top+=("$entry"); done < <(find "$package" -mindepth 1 -maxdepth 1 -print0)
  if [[ ${#top[@]} -eq 1 && -d "${top[0]}" && ! -L "${top[0]}" ]]; then package="${top[0]}"; fi
  "$ZIG" fetch "$package"
}

# Prefetch direct git dependencies as before. When Zig's Git transport cannot resolve/reach Codeberg,
# try its HTTPS commit archive instead; curl uses the system network stack and still verifies TLS.
if [[ -f build.zig.zon ]]; then
  while IFS= read -r url; do
    for ((try = 1; ; try++)); do
      if "$ZIG" fetch "$url" > "$WORK/fetch.log" 2>&1; then break; else status=$?; fi
      cat "$WORK/fetch.log" >&2
      if git_transport_failed "$WORK/fetch.log" && archive_url="$(codeberg_archive "$url")"; then
        fetch_archive "$url" "$archive_url"
        break
      fi
      (( try < 4 )) || { echo "Zig git dependency fetch failed $try times: $url" >&2; exit "$status"; }
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
  # Match observed transport diagnostics, not arbitrary compiler errors mentioning a reset or DNS.
  urls=()
  if git_transport_failed "$WORK/build.log"; then
    while IFS= read -r url; do urls+=("$url"); done < <(
      sed -n 's/.*\.url = "\(git+https:\/\/[^" ]*\)".*/\1/p' "$WORK/build.log" | sort -u
    )
  elif grep -Eq 'TlsInitializationFailed|: error: HTTP response read failure: ConnectionResetByPeer$' "$WORK/build.log"; then
    while IFS= read -r url; do urls+=("$url"); done < <(
      sed -n 's/.*\.url = "\(https:\/\/[^" ]*\)".*/\1/p' "$WORK/build.log" | sort -u
    )
  else
    exit "$status"
  fi
  [[ ${#urls[@]} -gt 0 ]] || exit "$status"
  for url in "${urls[@]}"; do
    archive_url="$url"
    if [[ "$url" == git+https://* ]]; then archive_url="$(codeberg_archive "$url")" || exit "$status"; fi
    # An unchanged failure after one fallback (including a mismatched package hash) must stay a failure.
    for prior in "${seen_urls[@]}"; do [[ "$prior" != "$url" ]] || exit "$status"; done
    fetch_archive "$url" "$archive_url"
  done
done
echo "too many Zig dependency transport failures" >&2
exit 1
