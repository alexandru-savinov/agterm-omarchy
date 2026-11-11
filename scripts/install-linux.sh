#!/usr/bin/env bash
# Install the complete relocatable payload without changing terminal defaults or agent hooks.
# Usage: scripts/install-linux.sh [--prefix DIR] [--no-build | --payload DIR] [--software-rendering | --default-rendering]
#        scripts/install-linux.sh [--prefix DIR] --uninstall [--purge-state]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
BUILD=true
PAYLOAD=""
UNINSTALL=false
PURGE_STATE=false
RENDERER_MODE=""
while (( $# )); do
  case "$1" in
    --prefix) [[ $# -ge 2 && -n "$2" ]] || { echo "--prefix needs a directory" >&2; exit 2; }; PREFIX="$2"; shift 2 ;;
    --no-build) BUILD=false; shift ;;
    --payload) [[ $# -ge 2 && -n "$2" ]] || { echo "--payload needs a directory" >&2; exit 2; }; PAYLOAD="$2"; shift 2 ;;
    --uninstall) UNINSTALL=true; shift ;;
    --purge-state) PURGE_STATE=true; shift ;;
    --software-rendering|--default-rendering)
      [[ -z "$RENDERER_MODE" ]] || { echo "choose only one renderer option" >&2; exit 2; }
      RENDERER_MODE="${1#--}"
      RENDERER_MODE="${RENDERER_MODE%-rendering}"
      shift ;;
    -h|--help)
      echo "usage: scripts/install-linux.sh [--prefix DIR] [--no-build | --payload DIR] [--software-rendering | --default-rendering]"
      echo "       scripts/install-linux.sh [--prefix DIR] --uninstall [--purge-state]"
      echo "Renderer selection persists across reinstalls; new installs use the inherited renderer environment."
      echo "--payload installs an extracted archive (its agterm-stage or agterm-linux directory) without building."
      echo "--uninstall removes the files the installer recorded; --purge-state also deletes agterm's state directory."
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$PREFIX" != *$'\n'* && "$PREFIX" != *$'\r'* ]] || { echo "invalid prefix" >&2; exit 2; }
if $UNINSTALL; then
  if [[ -n "$PAYLOAD" || -n "$RENDERER_MODE" ]] || ! $BUILD; then
    echo "--uninstall takes only --prefix and --purge-state" >&2; exit 2
  fi
elif $PURGE_STATE; then
  echo "--purge-state requires --uninstall" >&2; exit 2
elif [[ -n "$PAYLOAD" ]]; then
  $BUILD || { echo "--payload already skips the build; drop --no-build" >&2; exit 2; }
  [[ -x "$PAYLOAD/bin/agterm-linux" && -f "$PAYLOAD/share/applications/io.github.melonamin.agterm.desktop" ]] \
    || { echo "--payload needs an extracted agterm payload containing bin/agterm-linux" >&2; exit 2; }
  PAYLOAD="$(cd "$PAYLOAD" && pwd -P)"
  BUILD=false
fi
command -v python3 >/dev/null || { echo "python3 is required to install desktop entries" >&2; exit 1; }
# Files written outside lib/agterm: recorded in the payload at install, removed exactly at uninstall.
read -r -d '' DESKTOP_PY <<'PY' || true
import os, pathlib, shutil, sys
prefix = pathlib.Path(sys.argv[2])
payload = prefix / 'lib/agterm'
manifest_name = 'share/agterm/INSTALLED_FILES'
caches = ('share/icons/hicolor/icon-theme.cache', 'share/applications/mimeinfo.cache')

def derived(root):
    files = ['bin/agterm-linux', 'bin/agtermctl', 'share/applications/io.github.melonamin.agterm.desktop']
    for shared in ('share/icons', 'share/pixmaps'):
        base = root / shared
        files += sorted(f'{shared}/{path.relative_to(base)}' for path in base.rglob('*')
                        if path.is_file() or path.is_symlink()) if base.is_dir() else []
    return files

def recorded(root):
    # An installation predating the manifest still uninstalls exactly what its payload implies.
    manifest = root / manifest_name
    names = manifest.read_text().splitlines() if manifest.is_file() else derived(root)
    allowed = ('bin/', 'share/applications/', 'share/icons/', 'share/pixmaps/')
    for name in names:
        parts = pathlib.PurePosixPath(name).parts
        if not name.startswith(allowed) or '..' in parts or name.startswith('/'):
            sys.exit(f'refusing manifest entry outside the installer\'s paths: {name!r}')
    return names

def remove(names):
    for name in names:
        path = prefix / name
        if path.is_symlink() or path.is_file():
            path.unlink()

def prune(names):
    for name in sorted(names, key=lambda item: -item.count('/')):
        parent = (prefix / name).parent
        while parent != prefix and prefix in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

def install(previous):
    # A symlinked bin directory resolves relative links from its physical location.
    # Ordinary prefixes still use relative targets and can be moved as a whole.
    for name in ('agterm-linux', 'agtermctl'):
        link = prefix / 'bin' / name
        target = (payload / 'bin' / name).resolve()
        relative = os.path.relpath(target, link.parent.resolve())
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(relative)
    source = payload / 'share/applications/io.github.melonamin.agterm.desktop'
    launcher = str(prefix / 'bin/agterm-linux')
    # Desktop Entry Exec has its own escaping, separate from shell quoting; TryExec is a plain string value.
    executable = launcher.replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        executable = executable.replace(char, '\\' + char)
    executable = executable.replace('\\', '\\\\')
    try_exec = 'TryExec=' + launcher.replace('\\', '\\\\').replace('\t', '\\t')
    lines, group = [], None
    for line in source.read_text().splitlines():
        if line.startswith('['):
            group = line
        if line.startswith('TryExec='):
            continue
        if line.startswith('Exec=agterm-linux'):
            line = 'Exec="' + executable + '"' + line[len('Exec=agterm-linux'):]
            if group == '[Desktop Entry]':
                lines.append(try_exec)
        lines.append(line)
    (prefix / 'share/applications/io.github.melonamin.agterm.desktop').write_text('\n'.join(lines) + '\n')
    current = derived(payload)
    (payload / manifest_name).write_text('\n'.join(current) + '\n')
    if previous.is_dir():
        stale = sorted(set(recorded(previous)) - set(current))
        remove(stale)
        prune(stale)
    home = pathlib.Path(os.environ['HOME'])
    data_dirs = [os.environ.get('XDG_DATA_HOME') or str(home / '.local/share')]
    data_dirs += (os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':')
    if os.path.realpath(prefix / 'share') not in {os.path.realpath(item) for item in data_dirs if item}:
        print(f'warning: {prefix}/share is not on XDG_DATA_HOME or XDG_DATA_DIRS; '
              'application launchers will not list agterm', file=sys.stderr)

def uninstall():
    names = recorded(payload)
    remove(names)
    for cache in caches:
        directory = (prefix / cache).parent
        if (prefix / cache).is_file() and not any(
                path.is_file() and path != prefix / cache for path in directory.rglob('*')):
            (prefix / cache).unlink()
            names.append(cache)
    if payload.is_symlink():
        payload.unlink()
    else:
        shutil.rmtree(payload)
    prune(names + ['lib/agterm'])

if sys.argv[1] == 'install':
    install(pathlib.Path(sys.argv[3]))
else:
    uninstall()
PY
refresh_caches() {
  if [[ -d "$PREFIX/share/applications" ]] && command -v update-desktop-database >/dev/null; then
    update-desktop-database "$PREFIX/share/applications" || true
  fi
  if [[ -d "$PREFIX/share/icons/hicolor" ]] && command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" || true
  fi
}
if $UNINSTALL; then
  [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]] || { echo "no agterm installation in $PREFIX" >&2; exit 1; }
  PREFIX="$(cd "$PREFIX" && pwd)"
  python3 -c "$DESKTOP_PY" uninstall "$PREFIX"
  refresh_caches
  printf 'Removed agterm from %s; the renderer choice is not kept for a later install.\n' "$PREFIX"
  state="${XDG_DATA_HOME:-$HOME/.local/share}/agterm"
  if $PURGE_STATE; then
    rm -rf "$state"
    printf 'Deleted state in %s.\n' "$state"
  else
    printf 'Kept state in %s; --purge-state deletes it.\n' "$state"
  fi
  printf 'Kept configuration in %s.\n' "${XDG_CONFIG_HOME:-$HOME/.config}/agterm"
  exit 0
fi
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
if [[ -n "$PAYLOAD" ]]; then
  cp -a "$PAYLOAD" "$WORK/payload"
else
  "$ROOT/scripts/stage-linux.sh" "$WORK/payload"
fi
printf '%s\n' "$RENDERER_MODE" > "$WORK/payload/share/agterm/RENDERER"
# Finish staging before replacing an installed payload, so a failed build leaves it usable.
if [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]]; then
  mv "$PREFIX/lib/agterm" "$WORK/previous"
fi
if ! mv "$WORK/payload" "$PREFIX/lib/agterm"; then
  exit 1
fi
mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" "$PREFIX/share/icons" "$PREFIX/share/pixmaps"
python3 -c "$DESKTOP_PY" install "$PREFIX" "$WORK/previous"
if [[ -d "$PREFIX/lib/agterm/share/icons" ]]; then
  cp -R "$PREFIX/lib/agterm/share/icons/." "$PREFIX/share/icons/"
fi
cp -R "$PREFIX/lib/agterm/share/pixmaps/." "$PREFIX/share/pixmaps/"
refresh_caches
printf 'Installed agterm-linux and agtermctl in %s/bin (payload: %s/lib/agterm).\n' "$PREFIX" "$PREFIX"
printf 'Add %s/bin to PATH if needed.\n' "$PREFIX"
printf 'App renderer mode: %s (preserved on reinstall unless explicitly changed).\n' "$RENDERER_MODE"
