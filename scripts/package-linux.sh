#!/usr/bin/env bash
# Produce tar, DEB, and RPM artifacts from one staged release payload.
# Usage: scripts/package-linux.sh VERSION [OUTPUT_DIRECTORY]
# OUTPUT_DIRECTORY must be absent or empty; existing artifacts are never removed.
set -euo pipefail

if (( $# < 1 || $# > 2 )); then
  echo "usage: scripts/package-linux.sh VERSION [OUTPUT_DIRECTORY]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1#v}"
OUT="${2:-dist-linux}"
[[ "$OUT" = /* ]] || OUT="$ROOT/$OUT"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?(\+linux\.[1-9][0-9]*)?$ ]]; then
  echo "invalid package version: $1" >&2
  exit 2
fi

# Reject accidental project/home/output selection before invoking build dependencies.
check_output() (
  if [[ -L "$OUT" || ( -e "$OUT" && ! -d "$OUT" ) ]]; then
    echo "output must be a real directory, not a file or symlink: $OUT" >&2
    exit 2
  fi
  shopt -s nullglob dotglob
  entries=("$OUT"/*)
  if (( ${#entries[@]} )); then
    echo "output directory must be empty; existing files are preserved: $OUT" >&2
    exit 2
  fi
)
check_output

# shellcheck source=../linux/arch.sh
source "$ROOT/linux/arch.sh"

NFPM="${NFPM:-$(command -v nfpm || true)}"
[[ -x "$NFPM" ]] || { echo "nfpm is required to build DEB and RPM packages" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PAYLOAD="$WORK/agterm-linux"

ARTIFACTS="$WORK/artifacts"
mkdir -p "$ARTIFACTS"
AGTERM_PACKAGE_VERSION="$VERSION" "$ROOT/scripts/stage-linux.sh" "$PAYLOAD"

TAR="$ARTIFACTS/agterm-linux-v${VERSION}-${HOST_ARCH}.tar.gz"
DEB="$ARTIFACTS/agterm-linux-v${VERSION}-${HOST_ARCH}.deb"
RPM="$ARTIFACTS/agterm-linux-v${VERSION}-${HOST_ARCH}.rpm"

tar czf "$TAR" -C "$WORK" agterm-linux

export AGTERM_PACKAGE_VERSION="$VERSION"
export AGTERM_PACKAGE_ROOT="$PAYLOAD"
export AGTERM_PACKAGE_ARCH="$PACKAGE_ARCH"
"$NFPM" package --config "$ROOT/packaging/linux/nfpm.yml" --packager deb --target "$DEB"
"$NFPM" package --config "$ROOT/packaging/linux/nfpm.yml" --packager rpm --target "$RPM"

CHECKSUMS="$ARTIFACTS/agterm-linux-v${VERSION}-SHA256SUMS"
(
  cd "$ARTIFACTS"
  sha256sum "$(basename "$TAR")" "$(basename "$DEB")" "$(basename "$RPM")" > "$(basename "$CHECKSUMS")"
)

# Build/checksum failures leave the destination untouched. Recheck before publishing.
check_output
mkdir -p -- "$OUT"
mv -- "$TAR" "$DEB" "$RPM" "$CHECKSUMS" "$OUT/"
echo "→ Linux release artifacts"
du -h "$OUT/$(basename "$TAR")" "$OUT/$(basename "$DEB")" \
  "$OUT/$(basename "$RPM")" "$OUT/$(basename "$CHECKSUMS")"
