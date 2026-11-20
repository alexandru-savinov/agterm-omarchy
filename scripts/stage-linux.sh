#!/usr/bin/env bash
# Stage the relocatable agterm-linux payload shared by tar, DEB, RPM, and Flatpak packaging.
# Usage: scripts/stage-linux.sh DESTINATION
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: scripts/stage-linux.sh DESTINATION" >&2
  exit 2
fi

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
APP="$ROOT/agterm-linux"
DEST="$1"
[[ "$DEST" = /* ]] || DEST="$ROOT/$DEST"

# Resolve the embedded source commit before staging anything. A failing git lookup must never be
# recorded as an empty value, and a source archive inside an unrelated checkout must not borrow its HEAD.
if [[ -n "${AGTERM_PACKAGE_COMMIT:-}" ]]; then
  COMMIT="$AGTERM_PACKAGE_COMMIT"
  [[ "$COMMIT" =~ ^[[:graph:]]+$ ]] || {
    echo "AGTERM_PACKAGE_COMMIT must be a single word without whitespace" >&2
    exit 1
  }
else
  COMMIT=""
  if [[ "$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)" == "$(CDPATH='' cd -- "$ROOT" && pwd -P)" ]]; then
    COMMIT="$(git -C "$ROOT" rev-parse --verify -q HEAD || true)"
  fi
  [[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "cannot determine the source commit: $ROOT is not the top of a git checkout with a HEAD;" \
      "set AGTERM_PACKAGE_COMMIT to record one explicitly" >&2
    exit 1
  }
fi

if [[ -d "$DEST" ]] && find "$DEST" -mindepth 1 -print -quit | grep -q .; then
  echo "staging destination is not empty: $DEST" >&2
  exit 1
fi

BIN="$APP/.build/release/AgtermLinux"
CTL="$APP/.build/release/agtermctl-linux"
APP_RESOURCES="$APP/.build/release/agterm-linux_AgtermLinux.resources"
[[ -f "$BIN" ]] || {
  echo "no agterm-linux release build found — run 'swift build -c release' in agterm-linux/ first" >&2
  exit 1
}
[[ -f "$CTL" ]] || {
  echo "no agtermctl-linux release build found — run 'swift build -c release' in agterm-linux/ first" >&2
  exit 1
}
[[ -r "$APP_RESOURCES/hud/hud.sh" && -s "$APP_RESOURCES/live-prefill/.zshenv" ]] || {
  echo "no agterm-linux resource bundle found — run 'swift build -c release' in agterm-linux/ first" >&2
  exit 1
}

"$ROOT/scripts/verify-linux-resources.sh" "$APP/vendor/ghostty/share"
"$ROOT/scripts/verify-linux-zmx-cache.sh" "$APP/vendor/zmx"
python3 "$ROOT/scripts/linux-notices.py" verify-ghostty "$APP/vendor/ghostty/share/licenses/ghostty"

mkdir -p "$DEST/bin" "$DEST/lib" "$DEST/share/applications" "$DEST/share/pixmaps"
install -m755 "$BIN" "$DEST/bin/agterm-linux.bin"
install -m755 "$CTL" "$DEST/bin/agtermctl.bin"
install -m755 "$APP/vendor/zmx/zmx" "$DEST/bin/zmx"
# The linker records the toolchain and build-tree library paths; the payload resolves from its own lib/.
# shellcheck disable=SC2016 # $ORIGIN is the literal dynamic-loader token.
python3 "$ROOT/scripts/linux-runpath.py" set '$ORIGIN/../lib' "$DEST/bin/agterm-linux.bin" "$DEST/bin/agtermctl.bin"
cp -R "$APP_RESOURCES" "$DEST/bin/"
# Build the app-only EGL compatibility library on the payload's target architecture.
# Compiler and linker flags are whitespace-delimited, as in standard C build tools.
IFS=$' \t\n' read -r -d '' -a cflags <<< "${CFLAGS:-}" || true
IFS=$' \t\n' read -r -d '' -a ldflags <<< "${LDFLAGS:-}" || true
"${CC:-cc}" -std=c11 -O2 "${cflags[@]}" -fPIC -fvisibility=hidden -Wall -Wextra -Werror \
  -fstack-protector-strong -shared "${ldflags[@]}" -Wl,-z,defs,-z,relro,-z,now \
  -o "$DEST/lib/libagterm-egl-compat.so" "$ROOT/linux/egl-compat.c"

# Bundle the non-system libraries that make the Swift app portable. GTK, libadwaita, and glibc remain
# host dependencies in tar/DEB/RPM.
DEPENDENCIES="$(ldd "$BIN" && ldd "$CTL")"
if grep -q '=> not found' <<< "$DEPENDENCIES"; then
  printf 'cannot stage unresolved shared libraries:\n%s\n' "$DEPENDENCIES" >&2
  exit 1
fi
printf '%s\n' "$DEPENDENCIES" \
  | awk '/=> \// {print $3}' \
  | grep -E '/(swift|ghostty)|libghostty|swift-linux-compat|swift-compat' \
  | sort -u \
  | while read -r lib; do
      [[ -f "$lib" ]] && cp -L "$lib" "$DEST/lib/"
    done

cp -R "$APP/vendor/ghostty/share/ghostty" "$DEST/share/ghostty"
cp -R "$APP/vendor/ghostty/share/terminfo" "$DEST/share/terminfo"

[[ -d "$APP/Resources/icons" ]] && cp -R "$APP/Resources/icons" "$DEST/share/icons"
mkdir -p "$DEST/share/agterm"
cp -R "$ROOT/agterm/Resources/agent-status" "$DEST/share/agterm/agent-status"
cp -R "$ROOT/agterm/Resources/markdown" "$DEST/share/agterm/markdown"
cp -R "$ROOT/packaging/linux/licenses" "$DEST/share/agterm/licenses"
cp -R "$ROOT/plugins/agterm/skills/agterm" "$DEST/share/agterm/agent-skill"
install -m644 "$APP/vendor/zmx/LICENSE" "$DEST/share/agterm/ZMX-LICENSE"
python3 "$ROOT/scripts/linux-notices.py" stage "$DEST"
printf '%s\n' "${AGTERM_PACKAGE_VERSION:-dev}" > "$DEST/share/agterm/VERSION"
UPSTREAM_VERSION="${AGTERM_PACKAGE_VERSION:-dev}"
UPSTREAM_VERSION="${UPSTREAM_VERSION%%+linux.*}"
if [[ -r "$ROOT/packaging/linux/server/v$UPSTREAM_VERSION.sha256" ]]; then
  install -m644 "$ROOT/packaging/linux/server/v$UPSTREAM_VERSION.sha256" "$DEST/share/agterm/agterm-server.sha256"
  printf '%s\n' "$UPSTREAM_VERSION" > "$DEST/share/agterm/UPSTREAM_VERSION"
fi
printf '%s\n' "$COMMIT" > "$DEST/share/agterm/COMMIT"

DESKTOP="$ROOT/packaging/linux/io.github.melonamin.agterm.desktop"
install -m644 "$DESKTOP" "$DEST/share/applications/io.github.melonamin.agterm.desktop"

ICON_SRC="$(find "$ROOT/agterm/AppIcon.icon/Assets" -maxdepth 1 -type f -name '*.png' -print -quit)"
IMAGE_CONVERTER="$(command -v magick || command -v convert || true)"
# macOS masks the icon's corners at display time, while a Linux shell shows a PNG as drawn.
rounded_icon() {
  local size="$1" out="$2" radius
  radius=$(( (size * 224 + 500) / 1000 ))
  "$IMAGE_CONVERTER" "$ICON_SRC" -resize "${size}x${size}!" -alpha set \
    \( -size "${size}x${size}" xc:none -fill white \
       -draw "roundrectangle 0,0,$((size - 1)),$((size - 1)),${radius},${radius}" \) \
    -compose DstIn -composite "$out"
}
if [[ -n "$ICON_SRC" && -n "$IMAGE_CONVERTER" ]]; then
  for size in 16 32 48 64 128 256 512; do
    ICON_DIR="$DEST/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$ICON_DIR"
    rounded_icon "$size" "$ICON_DIR/io.github.melonamin.agterm.png"
  done
  install -m644 "$DEST/share/icons/hicolor/256x256/apps/io.github.melonamin.agterm.png" \
    "$DEST/share/pixmaps/io.github.melonamin.agterm.png"
elif [[ -n "$ICON_SRC" ]]; then
  install -m644 "$ICON_SRC" "$DEST/share/pixmaps/io.github.melonamin.agterm.png"
fi

cat > "$DEST/bin/agterm-linux" <<'LAUNCH'
#!/usr/bin/env bash
HERE="$(CDPATH='' cd -- "$(dirname "$(readlink -f "$0")")/.." && pwd)"
# Every variable set here is agterm's own. Before overriding one, record the caller's value as
# AGTERM_ORIG_<NAME>, or list the name in AGTERM_ORIG_UNSET when the caller had none; the app hands those
# back to every pane, hook, command and zmx daemon (LauncherChildEnvironment). Bookkeeping inherited from
# an outer agterm is cleared first so it can never describe this launch, and AGTERM_ORIG_SET=1 says this
# record was written by a launcher: a pane receives it as "", so an agterm started there WITHOUT the
# launcher ignores the stale record instead of restoring its blanks.
for name in "${!AGTERM_ORIG_@}"; do unset "$name"; done
export AGTERM_ORIG_SET=1
AGTERM_ORIG_UNSET=""
agterm_set() {
  if [[ -v $1 ]]; then export "AGTERM_ORIG_$1=${!1}"; else AGTERM_ORIG_UNSET+="${AGTERM_ORIG_UNSET:+ }$1"; fi
  export "$1=$2"
}
agterm_set LD_LIBRARY_PATH "$HERE/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[[ -d "$HERE/share/ghostty" ]] && agterm_set AGTERM_GHOSTTY_RESOURCES "$HERE/share/ghostty"
[[ -f "$HERE/share/agterm/VERSION" ]] && agterm_set AGTERM_VERSION "$(cat "$HERE/share/agterm/VERSION")"
[[ -f "$HERE/share/agterm/COMMIT" ]] && agterm_set AGTERM_COMMIT "$(cat "$HERE/share/agterm/COMMIT")"
agterm_set AGTERM_ZMX_PATH "$HERE/bin/zmx"
# This is a data marker, never shell configuration. Default/absent values preserve the caller's choice.
if [[ -f "$HERE/share/agterm/RENDERER" ]] && [[ "$(cat "$HERE/share/agterm/RENDERER")" == software ]]; then
  agterm_set LIBGL_ALWAYS_SOFTWARE 1
fi
# $ORIGIN is expanded by the loader after it parses LD_PRELOAD, including in prefixes with spaces.
# LD_PRELOAD must remain the last assignment before exec.
# shellcheck disable=SC2016
if [[ "${AGTERM_DISABLE_EGL_COMPAT:-}" != 1 && -r "$HERE/lib/libagterm-egl-compat.so" ]]; then
  agterm_set LD_PRELOAD '$ORIGIN/../lib/libagterm-egl-compat.so'"${LD_PRELOAD:+:$LD_PRELOAD}"
fi
export AGTERM_ORIG_UNSET
exec "$HERE/bin/agterm-linux.bin" "$@"
LAUNCH
chmod 0755 "$DEST/bin/agterm-linux"

cat > "$DEST/bin/agtermctl" <<'LAUNCH'
#!/usr/bin/env bash
HERE="$(CDPATH='' cd -- "$(dirname "$(readlink -f "$0")")/.." && pwd)"
export LD_LIBRARY_PATH="$HERE/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/bin/agtermctl.bin" "$@"
LAUNCH
chmod 0755 "$DEST/bin/agtermctl"

python3 "$ROOT/scripts/linux-runpath.py" check "$DEST"/bin/* "$DEST"/lib/*

echo "→ staged agterm-linux payload at $DEST"
