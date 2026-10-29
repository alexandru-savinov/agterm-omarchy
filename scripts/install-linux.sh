#!/usr/bin/env bash
# Install the complete relocatable payload without changing terminal defaults or agent hooks.
# Usage: scripts/install-linux.sh [--prefix DIR] [--no-build]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
BUILD=true
while (( $# )); do
  case "$1" in
    --prefix) [[ $# -ge 2 && -n "$2" ]] || { echo "--prefix needs a directory" >&2; exit 2; }; PREFIX="$2"; shift 2 ;;
    --no-build) BUILD=false; shift ;;
    -h|--help) echo "usage: scripts/install-linux.sh [--prefix DIR] [--no-build]"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$PREFIX" != *$'\n'* && "$PREFIX" != *$'\r'* ]] || { echo "invalid prefix" >&2; exit 2; }
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
trap 'rm -rf "$WORK"' EXIT
"$ROOT/scripts/stage-linux.sh" "$WORK/payload"
# Finish staging before replacing an installed payload, so a failed build leaves it usable.
if [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]]; then
  mv "$PREFIX/lib/agterm" "$WORK/previous"
fi
if ! mv "$WORK/payload" "$PREFIX/lib/agterm"; then
  [[ ! -e "$WORK/previous" ]] || mv "$WORK/previous" "$PREFIX/lib/agterm"
  exit 1
fi
mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" "$PREFIX/share/icons" "$PREFIX/share/pixmaps"
ln -sfn ../lib/agterm/bin/agterm-linux "$PREFIX/bin/agterm-linux"
ln -sfn ../lib/agterm/bin/agtermctl "$PREFIX/bin/agtermctl"
python3 - "$PREFIX" <<'PY'
import pathlib, sys
prefix = pathlib.Path(sys.argv[1])
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
