#!/usr/bin/env bash
# Install the complete relocatable payload without changing terminal defaults or agent hooks.
# Usage: scripts/install-linux.sh [--prefix DIR] [--no-build | --payload DIR] [--software-rendering | --default-rendering]
#        scripts/install-linux.sh [--prefix DIR] --uninstall [--purge-state]
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
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
  PAYLOAD="$(CDPATH='' cd -- "$PAYLOAD" && pwd -P)"
  BUILD=false
fi
if ! command -v python3 >/dev/null; then
  if $UNINSTALL; then
    echo "python3 is required to uninstall: it reads the install manifest that lists the files to remove" >&2
  else
    echo "python3 is required to install desktop entries" >&2
  fi
  exit 1
fi
# Files written outside lib/agterm: recorded in the payload at install, removed exactly at uninstall.
read -r -d '' DESKTOP_PY <<'PY' || true
import json, os, pathlib, shutil, sys
prefix = pathlib.Path(sys.argv[2])
payload = prefix / 'lib/agterm'
manifest_name = 'share/agterm/INSTALLED_FILES'
# Directories this installer created below the prefix; only these may be pruned when they empty.
dirs_name = 'share/agterm/INSTALLED_DIRS'
launchers = ('bin/agterm-linux', 'bin/agtermctl')
desktop_entry = 'share/applications/io.github.melonamin.agterm.desktop'
caches = ('share/icons/hicolor/icon-theme.cache', 'share/applications/mimeinfo.cache')

def derived(root):
    files = [*launchers, desktop_entry]
    for shared in ('share/icons', 'share/pixmaps'):
        base = root / shared
        files += sorted(f'{shared}/{path.relative_to(base)}' for path in base.rglob('*')
                        if path.is_file() or path.is_symlink()) if base.is_dir() else []
    return files

def manifest_problem(manifest):
    # The installer always writes newline-terminated lines that include both launchers and the entry.
    if not manifest.is_file():
        return 'malformed', 'it is not a regular file'
    text = manifest.read_text()
    names = text.splitlines()
    if not text:
        return 'incomplete', 'it is empty'
    if not text.endswith('\n'):
        return 'incomplete', 'it ends mid-line'
    if any(not name for name in names) or len(set(names)) != len(names):
        return 'malformed', 'it has blank or duplicate entries'
    checked(names)
    if not set(launchers + (desktop_entry,)) <= set(names):
        return 'incomplete', 'it does not list both launchers and the desktop entry'
    return None

def checked(names):
    for name in names:
        parts = pathlib.PurePosixPath(name).parts
        inside = name in launchers or name.startswith(('share/applications/', 'share/icons/', 'share/pixmaps/'))
        if not inside or '..' in parts or name.startswith('/'):
            sys.exit(f'refusing manifest entry outside the installer\'s paths: {name!r}')
    return names

def recorded(root, strict=True):
    # An installation predating the manifest still uninstalls exactly what its payload implies.
    manifest = root / manifest_name
    if not (manifest.exists() or manifest.is_symlink()):
        return checked(derived(root))
    problem = manifest_problem(manifest)
    if problem is None:
        return manifest.read_text().splitlines()
    kind, detail = problem
    if strict:
        sys.exit(f'refusing to uninstall: {manifest} is {kind} ({detail}); nothing was removed')
    # Replacing an install only needs its stale launcher files; its payload still implies them.
    print(f'warning: ignoring {kind} {manifest} ({detail}); using the installed payload instead',
          file=sys.stderr)
    return checked(derived(root))

def created_dirs(root):
    record = root / dirs_name
    # Installations predating the record never prune: their directories may predate agterm.
    names = record.read_text().splitlines() if record.is_file() else []
    for name in names:
        parts = pathlib.PurePosixPath(name).parts
        inside = name in ('bin', 'lib', 'share') or name.startswith(('share/applications', 'share/icons',
                                                                      'share/pixmaps'))
        if not inside or '..' in parts or name.startswith('/'):
            sys.exit(f'refusing directory record outside the installer\'s paths: {name!r}')
    return set(names)

def remove(names):
    for name in names:
        path = prefix / name
        if path.is_symlink() or path.is_file():
            path.unlink()

def prune(names, created):
    for name in sorted(names, key=lambda item: -item.count('/')):
        parent = (prefix / name).parent
        while parent != prefix and prefix in parent.parents:
            if parent.relative_to(prefix).as_posix() not in created:
                break
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

def install(previous):
    current = derived(payload)
    created = set(json.loads((previous.parent / 'created.json').read_text()))
    if previous.is_dir():
        created |= created_dirs(previous)
    remove(current)
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
    (payload / manifest_name).unlink(missing_ok=True)
    (payload / manifest_name).write_text('\n'.join(current) + '\n')
    (payload / dirs_name).unlink(missing_ok=True)
    (payload / dirs_name).write_text(''.join(name + '\n' for name in sorted(created)))
    if previous.is_dir():
        stale = sorted(set(recorded(previous, strict=False)) - set(current))
        remove(stale)
        prune(stale, created)
    home = pathlib.Path(os.environ['HOME'])
    data_dirs = [os.environ.get('XDG_DATA_HOME') or str(home / '.local/share')]
    data_dirs += (os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':')
    if os.path.realpath(prefix / 'share') not in {os.path.realpath(item) for item in data_dirs if item}:
        print(f'warning: {prefix}/share is not on XDG_DATA_HOME or XDG_DATA_DIRS; '
              'application launchers will not list agterm', file=sys.stderr)

def uninstall():
    names = recorded(payload)
    created = created_dirs(payload)
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
    prune(names + ['lib/agterm'], created)

def snapshot(work):
    staged = (work / 'payload').resolve()
    for name in ('agterm-linux', 'agterm-linux.bin', 'agtermctl', 'agtermctl.bin', 'zmx'):
        path = staged / 'bin' / name
        if not path.is_file() or not os.access(path, os.X_OK) or not path.resolve().is_relative_to(staged):
            sys.exit(f'incomplete payload: missing executable {path}')
    for name in ('lib', 'share/agterm', 'share/ghostty', 'share/terminfo', 'share/pixmaps'):
        if not (staged / name).is_dir() or not (staged / name).resolve().is_relative_to(staged):
            sys.exit(f'incomplete payload: missing directory {name}')
    desktop = staged / 'share/applications/io.github.melonamin.agterm.desktop'
    desktop.read_text()
    names = sorted(set(derived(staged) + (recorded(payload, strict=False) if payload.exists() else [])))
    # Record, before anything is created, which directories this attempt adds below the prefix.
    candidates = {'bin', 'share/applications', 'share/icons', 'share/pixmaps'}
    for name in derived(staged):
        candidates.update(parent.as_posix() for parent in pathlib.PurePosixPath(name).parents
                          if parent.as_posix() != '.')
    created = {name for name in candidates if not (prefix / name).exists() and not (prefix / name).is_symlink()}
    if (work / 'created-lib').exists():
        created.add('lib')
    (work / 'created.json').write_text(json.dumps(sorted(created)))
    for name in names:
        source = prefix / name
        if source.is_symlink() or source.is_file():
            backup = work / 'external' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup, follow_symlinks=False)
        elif source.exists():
            sys.exit(f'refusing non-file installation target: {source}')
    (work / 'external.json').write_text(json.dumps(names))

def restore(work):
    names = json.loads((work / 'external.json').read_text())
    remove(names)
    for name in names:
        backup = work / 'external' / name
        if backup.is_symlink() or backup.is_file():
            destination = prefix / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination, follow_symlinks=False)
    created_file = work / 'created.json'
    prune(names + ['lib/agterm'], set(json.loads(created_file.read_text())) if created_file.is_file() else set())

if sys.argv[1] == 'snapshot':
    snapshot(pathlib.Path(sys.argv[3]))
elif sys.argv[1] == 'restore':
    restore(pathlib.Path(sys.argv[3]))
elif sys.argv[1] == 'install':
    install(pathlib.Path(sys.argv[3]))
else:
    uninstall()
PY
# A directory with nothing left to index gets no cache: regenerating one after uninstall would
# leave a file agterm caused in a directory that existed before agterm.
refresh_caches() {
  if [[ -d "$PREFIX/share/applications" ]] && command -v update-desktop-database >/dev/null \
    && [[ -n "$(find "$PREFIX/share/applications" -name '*.desktop' -print -quit)" ]]; then
    update-desktop-database "$PREFIX/share/applications" || true
  fi
  if [[ -d "$PREFIX/share/icons/hicolor" ]] && command -v gtk-update-icon-cache >/dev/null \
    && [[ -n "$(find "$PREFIX/share/icons/hicolor" ! -type d ! -name icon-theme.cache -print -quit)" ]]; then
    gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" || true
  fi
}
if $UNINSTALL; then
  [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]] || { echo "no agterm installation in $PREFIX" >&2; exit 1; }
  PREFIX="$(CDPATH='' cd -- "$PREFIX" && pwd)"
  python3 -I -c "$DESKTOP_PY" uninstall "$PREFIX"
  refresh_caches
  printf 'Removed agterm from %s; the renderer choice is not kept for a later install.\n' "$PREFIX"
  state="${XDG_DATA_HOME:-$HOME/.local/share}/agterm"
  if $PURGE_STATE; then
    rm -rf "$state"
    printf 'Deleted state in %s.\n' "$state"
  else
    printf 'Kept state in %s; --purge-state deletes it.\n' "$state"
  fi
  # The app reads ~/.config/agterm regardless of XDG_CONFIG_HOME (agtermCore ConfigPaths).
  printf 'Kept configuration in %s.\n' "$HOME/.config/agterm"
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

LIB_CREATED=false
[[ -e "$PREFIX/lib" || -L "$PREFIX/lib" ]] || LIB_CREATED=true
mkdir -p "$PREFIX/lib"
PREFIX="$(CDPATH='' cd -- "$PREFIX" && pwd)"
WORK="$(mktemp -d "$PREFIX/lib/.agterm-install.XXXXXX")"
! $LIB_CREATED || : > "$WORK/created-lib"
COMMITTED=false
cleanup() {
  local status=$?
  trap - EXIT
  trap '' HUP INT TERM
  if ! $COMMITTED && [[ -f "$WORK/external.json" ]]; then
    if [[ ! -e "$WORK/payload" ]] && [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]]; then
      if ! mv "$PREFIX/lib/agterm" "$WORK/failed"; then
        printf 'Could not roll back installation; recovery files retained in %s.\n' "$WORK" >&2
        exit 1
      fi
    fi
    if [[ -e "$WORK/previous" || -L "$WORK/previous" ]]; then
      if ! mv "$WORK/previous" "$PREFIX/lib/agterm"; then
        printf 'Could not restore previous installation; recover it from %s/previous.\n' "$WORK" >&2
        exit 1
      fi
    fi
    if ! python3 -I -c "$DESKTOP_PY" restore "$PREFIX" "$WORK"; then
      printf 'Could not restore launcher files; recovery files retained in %s.\n' "$WORK" >&2
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
python3 -I -c "$DESKTOP_PY" snapshot "$PREFIX" "$WORK"
rm -f "$WORK/payload/share/agterm/RENDERER"
printf '%s\n' "$RENDERER_MODE" > "$WORK/payload/share/agterm/RENDERER"
# Finish staging before replacing an installed payload, so a failed build leaves it usable.
if [[ -e "$PREFIX/lib/agterm" || -L "$PREFIX/lib/agterm" ]]; then
  mv "$PREFIX/lib/agterm" "$WORK/previous"
fi
if ! mv "$WORK/payload" "$PREFIX/lib/agterm"; then
  exit 1
fi
mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" "$PREFIX/share/icons" "$PREFIX/share/pixmaps"
python3 -I -c "$DESKTOP_PY" install "$PREFIX" "$WORK/previous"
if [[ -d "$PREFIX/lib/agterm/share/icons" ]]; then
  cp -R "$PREFIX/lib/agterm/share/icons/." "$PREFIX/share/icons/"
fi
cp -R "$PREFIX/lib/agterm/share/pixmaps/." "$PREFIX/share/pixmaps/"
COMMITTED=true
refresh_caches
printf 'Installed agterm-linux and agtermctl in %s/bin (payload: %s/lib/agterm).\n' "$PREFIX" "$PREFIX"
printf 'Add %s/bin to PATH if needed.\n' "$PREFIX"
printf 'App renderer mode: %s (preserved on reinstall unless explicitly changed).\n' "$RENDERER_MODE"
