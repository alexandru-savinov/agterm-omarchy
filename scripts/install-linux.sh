#!/usr/bin/env bash
# Install the complete relocatable payload without changing terminal defaults or agent hooks.
# Usage: scripts/install-linux.sh [--prefix DIR] [--no-build] [--software-rendering | --default-rendering]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
BUILD=true
RENDERER_MODE=""
while (( $# )); do
  case "$1" in
    --prefix) [[ $# -ge 2 && -n "$2" ]] || { echo "--prefix needs a directory" >&2; exit 2; }; PREFIX="$2"; shift 2 ;;
    --no-build) BUILD=false; shift ;;
    --software-rendering|--default-rendering)
      [[ -z "$RENDERER_MODE" ]] || { echo "choose only one renderer option" >&2; exit 2; }
      RENDERER_MODE="${1#--}"
      RENDERER_MODE="${RENDERER_MODE%-rendering}"
      shift ;;
    -h|--help)
      echo "usage: scripts/install-linux.sh [--prefix DIR] [--no-build] [--software-rendering | --default-rendering]"
      echo "Renderer selection persists across reinstalls; new installs use the inherited renderer environment."
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$PREFIX" != *$'\n'* && "$PREFIX" != *$'\r'* ]] || { echo "invalid prefix" >&2; exit 2; }
if [[ -z "$RENDERER_MODE" ]]; then
  RENDERER_MODE=default
  if [[ -f "$PREFIX/lib/agterm/share/agterm/RENDERER" ]]; then
    RENDERER_MODE="$(cat "$PREFIX/lib/agterm/share/agterm/RENDERER")"
  fi
fi
case "$RENDERER_MODE" in
  software|default) ;;
  *) echo "invalid installed renderer mode; select --software-rendering or --default-rendering" >&2; exit 2 ;;
esac
command -v python3 >/dev/null || { echo "python3 is required to install desktop entries" >&2; exit 1; }
# shellcheck source=../linux/swift-env.sh
if $BUILD; then
  source "$ROOT/linux/swift-env.sh"
  (cd "$ROOT/agterm-linux" && "${SWIFT_COMMAND[@]}" build -c release)
else
  SWIFT_COMPAT="${XDG_DATA_HOME:-$HOME/.local/share}/agterm/swift-compat"
  [[ ! -d "$SWIFT_COMPAT" ]] || export LD_LIBRARY_PATH="$SWIFT_COMPAT${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

mkdir -p "$PREFIX/lib"
PREFIX="$(cd "$PREFIX" && pwd)"
WORK="$(mktemp -d "$PREFIX/lib/.agterm-install.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  trap '' HUP INT TERM
  # Inspect paths rather than a flag: a signal can arrive immediately after either rename.
  if [[ ! -e "$PREFIX/lib/agterm" && ! -L "$PREFIX/lib/agterm" ]] \
      && [[ -e "$WORK/previous" || -L "$WORK/previous" ]]; then
    if ! mv "$WORK/previous" "$PREFIX/lib/agterm"; then
      printf 'Could not restore previous installation; recover it from %s/previous.\n' "$WORK" >&2
      exit 1
    fi
  fi
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
"$ROOT/scripts/stage-linux.sh" "$WORK/payload"
printf '%s\n' "$RENDERER_MODE" > "$WORK/payload/share/agterm/RENDERER"
# Finish staging before replacing an installed payload, so a failed build leaves it usable.
if [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]]; then
  mv "$PREFIX/lib/agterm" "$WORK/previous"
fi
if ! mv "$WORK/payload" "$PREFIX/lib/agterm"; then
  exit 1
fi
mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" "$PREFIX/share/icons" "$PREFIX/share/pixmaps"
python3 - "$PREFIX" <<'PY'
import os, pathlib, sys
prefix = pathlib.Path(sys.argv[1])
# A symlinked bin directory resolves relative links from its physical location.
# Ordinary prefixes still use relative targets and can be moved as a whole.
for name in ('agterm-linux', 'agtermctl'):
    link = prefix / 'bin' / name
    target = (prefix / 'lib/agterm/bin' / name).resolve()
    relative = os.path.relpath(target, link.parent.resolve())
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(relative)
source = prefix / 'lib/agterm/share/applications/io.github.melonamin.agterm.desktop'
# Desktop Entry Exec has its own escaping, separate from shell quoting.
executable = str(prefix / 'bin/agterm-linux').replace('%', '%%')
for char in ('\\', '"', '`', '$'):
    executable = executable.replace(char, '\\' + char)
executable = executable.replace('\\', '\\\\')
lines = []
for line in source.read_text().splitlines():
    if line.startswith('Exec=agterm-linux'):
        line = 'Exec="' + executable + '"' + line[len('Exec=agterm-linux'):]
    lines.append(line)
(prefix / 'share/applications/io.github.melonamin.agterm.desktop').write_text('\n'.join(lines) + '\n')
PY
if [[ -d "$PREFIX/lib/agterm/share/icons" ]]; then
  cp -R "$PREFIX/lib/agterm/share/icons/." "$PREFIX/share/icons/"
fi
cp -R "$PREFIX/lib/agterm/share/pixmaps/." "$PREFIX/share/pixmaps/"
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$PREFIX/share/applications" || true
fi
if [[ -d "$PREFIX/share/icons/hicolor" ]] && command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" || true
fi
printf 'Installed agterm-linux and agtermctl in %s/bin (payload: %s/lib/agterm).\n' "$PREFIX" "$PREFIX"
printf 'Add %s/bin to PATH if needed.\n' "$PREFIX"
printf 'App renderer mode: %s (preserved on reinstall unless explicitly changed).\n' "$RENDERER_MODE"
