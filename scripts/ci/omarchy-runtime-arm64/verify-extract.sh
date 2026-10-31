#!/usr/bin/env bash
# Verify the downloaded aarch64 archive against the expected sha256 and extract it into a
# fresh empty directory. Prints the identity lines used by every job's summary.
# Usage: verify-extract.sh <download-dir> <expected-sha256> <dest-dir>
set -euo pipefail
dl="${1:?download dir}"
want="${2:?sha256}"
dest="${3:?dest}"
[[ "$want" =~ ^[0-9a-f]{64}$ ]] || { echo "::error::expected sha256 is not 64 hex characters"; exit 2; }
mapfile -t archives < <(find "$dl" -maxdepth 1 -type f -name '*.tar.gz')
[[ ${#archives[@]} -eq 1 ]] || { echo "::error::expected exactly one .tar.gz in the artifact, found ${#archives[@]}"; exit 2; }
archive="${archives[0]}"
got="$(sha256sum "$archive" | cut -d' ' -f1)"
echo "archive=$(basename "$archive") sha256=$got"
if [[ "$got" != "$want" ]]; then
  echo "::error::archive sha256 mismatch: got $got want $want"
  exit 1
fi
echo "archive sha256 verified"
[[ ! -e "$dest" ]] || { echo "::error::extraction directory must not exist yet"; exit 2; }
mkdir -p "$dest"
# Refuse absolute or parent-relative members before extracting.
if tar -tzf "$archive" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
  echo "::error::archive contains absolute or parent-relative paths"; exit 1
fi
tar -xzf "$archive" -C "$dest" --no-same-owner
stage="$dest/agterm-stage"
[[ -x "$stage/bin/agterm-linux" && -x "$stage/bin/zmx" ]] || { echo "::error::unexpected payload layout"; exit 1; }
echo "payload COMMIT=$(cat "$stage/share/agterm/COMMIT" 2>/dev/null || echo missing) VERSION=$(cat "$stage/share/agterm/VERSION" 2>/dev/null || echo missing)"
echo "zmx sha256=$(sha256sum "$stage/bin/zmx" | cut -d' ' -f1)"
echo "app sha256=$(sha256sum "$stage/bin/agterm-linux.bin" | cut -d' ' -f1)"
