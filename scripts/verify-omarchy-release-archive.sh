#!/usr/bin/env bash
# Natively check a versioned release archive with the system tools users and packagers run:
# GNU tar ownership, VERSION, readelf RUNPATH, and ldd resolution from the archive's own lib/.
# Usage: scripts/verify-omarchy-release-archive.sh ARCHIVE VERSION
# shellcheck disable=SC2016 # $ORIGIN is the literal dynamic-loader token throughout.
set -euo pipefail

if (( $# != 2 )); then
  echo "usage: scripts/verify-omarchy-release-archive.sh ARCHIVE VERSION" >&2
  exit 2
fi
ARCHIVE="$1"
VERSION="$2"
for command in tar readelf ldd; do command -v "$command" >/dev/null; done

owners="$(tar --numeric-owner -tvzf "$ARCHIVE" | awk '{print $2}' | sort -u)"
[[ "$owners" == "0/0" ]] || { printf 'archive entries are not all 0/0:\n%s\n' "$owners" >&2; exit 1; }
echo "ownership: every entry 0/0 ($(tar -tzf "$ARCHIVE" | wc -l) entries)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$ARCHIVE" -C "$WORK"
STAGE="$WORK/agterm-stage"
[[ "$(cat "$STAGE/share/agterm/VERSION")" == "$VERSION" ]] || { echo "VERSION is not $VERSION" >&2; exit 1; }
echo "VERSION: $VERSION"
# `agtermctl server install` refuses a payload without the upstream release it fetches the server from.
UPSTREAM="$(cat "$STAGE/share/agterm/UPSTREAM_VERSION" 2>/dev/null || true)"
[[ -n "$UPSTREAM" && "$VERSION" == "$UPSTREAM".* ]] || { echo "UPSTREAM_VERSION '$UPSTREAM' does not match $VERSION" >&2; exit 1; }
grep -qE '^[0-9a-f]{64}  agterm-server-' "$STAGE/share/agterm/agterm-server.sha256" \
  || { echo "agterm-server.sha256 is missing or lists no agterm-server archive" >&2; exit 1; }
echo "UPSTREAM_VERSION: $UPSTREAM"

for binary in agterm-linux.bin agtermctl.bin; do
  paths="$(readelf -d "$STAGE/bin/$binary" | grep -E '\(R(UN)?PATH\)' || true)"
  if [[ "$(wc -l <<< "$paths")" != 1 ]] || ! grep -qE '\(RUNPATH\) +Library runpath: \[\$ORIGIN/\.\./lib\]$' <<< "$paths"; then
    printf '%s RUNPATH is not $ORIGIN/../lib:\n%s\n' "$binary" "$paths" >&2
    exit 1
  fi
  echo "$binary:$paths"
done
while IFS= read -r -d '' file; do
  [[ "$(head -c4 "$file")" == $'\x7fELF' ]] || continue
  if readelf -d "$file" | grep -E '\(R(UN)?PATH\)' | grep -vE '\[\$ORIGIN[^]:]*(:\$ORIGIN[^]:]*)*\]$'; then
    echo "absolute search path in ${file#"$STAGE"/}" >&2
    exit 1
  fi
done < <(find "$STAGE/bin" "$STAGE/lib" -type f -print0)

# CI's build paths still exist on this runner, so a successful launch alone proves nothing:
# every library the archive bundles must resolve to the archive's copy with LD_LIBRARY_PATH unset.
for binary in agterm-linux.bin agtermctl.bin; do
  resolved="$(env -u LD_LIBRARY_PATH ldd "$STAGE/bin/$binary")"
  bundled=()
  while read -r name path; do
    [[ -f "$STAGE/lib/$name" ]] || continue
    [[ "$(readlink -f "$path")" == "$(readlink -f "$STAGE/lib/$name")" ]] || {
      printf '%s resolves bundled %s outside the archive: %s\n' "$binary" "$name" "$path" >&2
      exit 1
    }
    bundled+=("$name")
  done < <(awk '$2 == "=>" {print $1, $3}' <<< "$resolved")
  required=(libswiftCore.so)
  [[ "$binary" == agterm-linux.bin ]] && required+=(libghostty.so)
  for name in "${required[@]}"; do
    [[ " ${bundled[*]} " == *" $name "* ]] || { echo "$binary does not load $name from the archive" >&2; exit 1; }
  done
  echo "$binary: ${#bundled[@]} bundled libraries resolve to lib/ without LD_LIBRARY_PATH: ${bundled[*]}"
done
env -u LD_LIBRARY_PATH "$STAGE/bin/agtermctl.bin" --help >/dev/null
echo "agtermctl.bin --help runs without LD_LIBRARY_PATH"
echo "remaining CI path strings outside the dynamic section (debug info): $(cat "$STAGE"/bin/*.bin | { grep -ao '/__w/' || true; } | wc -l)"
