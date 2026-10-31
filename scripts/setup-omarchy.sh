#!/usr/bin/env bash
# Provision native Arch/Omarchy build dependencies and the pinned Linux runtimes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
skip_packages=false
ui_tests=false
for argument in "$@"; do
  case "$argument" in
    --skip-packages) skip_packages=true ;;
    --with-ui-tests) ui_tests=true ;;
    -h|--help)
      echo "usage: scripts/setup-omarchy.sh [--skip-packages] [--with-ui-tests]"
      echo "Installs Arch packages (full system upgrade), Swift 6.3.2 and Zig 0.16.0; builds Linux runtimes."
      exit 0 ;;
    *) echo "unknown argument: $argument" >&2; exit 2 ;;
  esac
done

[[ "$(uname -s)" == Linux ]] || { echo "run this script inside Omarchy/Arch Linux" >&2; exit 1; }
[[ "$EUID" != 0 ]] || { echo "run as your desktop user; only pacman uses sudo" >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
  *" arch "*|*" archarm "*) ;;
  *) echo "this setup requires Arch Linux or an Arch derivative" >&2; exit 1 ;;
esac
# shellcheck source=../linux/arch.sh
source "$ROOT/linux/arch.sh"

if ! $skip_packages; then
  packages=(base-devel git curl ca-certificates xz pkgconf file ncurses libxml2-legacy
    libbsd libedit sqlite zlib gtk4 libadwaita webkitgtk-6.0 libepoxy mise
    imagemagick desktop-file-utils python zsh)
  if $ui_tests; then
    packages+=(xorg-server-xvfb xorg-xauth openbox xdotool python-gobject at-spi2-core grim)
  fi
  # Refreshing Arch databases without upgrading can mix incompatible library versions.
  sudo pacman -Syu --needed "${packages[@]}"
fi
for dependency in mise pkg-config; do
  command -v "$dependency" >/dev/null || { echo "missing dependency: $dependency" >&2; exit 1; }
done
pkg-config --exists gtk4 libadwaita-1 webkitgtk-6.0 epoxy
[[ -e /usr/lib/libxml2.so.2 ]] || {
  echo "install libxml2-legacy; do not alias libxml2.so.2 to a different ABI" >&2
  exit 1
}

# Swift's Ubuntu build expects the non-wide ncurses names; keep aliases private to its tools.
compat="${XDG_DATA_HOME:-$HOME/.local/share}/agterm/swift-compat"
mkdir -p "$compat"
for library in ncurses form panel; do
  if [[ ! -e "/usr/lib/lib${library}.so.6" ]]; then
    [[ -e "/usr/lib/lib${library}w.so.6" ]] || {
      echo "missing lib${library}w.so.6; install ncurses" >&2
      exit 1
    }
    ln -sfn "/usr/lib/lib${library}w.so.6" "$compat/lib${library}.so.6"
  fi
done
export LD_LIBRARY_PATH="$compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Older mise releases otherwise request a nonexistent arch/archarm Swift archive.
MISE_SWIFT_PLATFORM=ubuntu24.04 mise install swift@6.3.2
if [[ "$(zig version 2>/dev/null || true)" != 0.16.0 ]]; then
  mise install zig@0.16.0
fi
mise exec swift@6.3.2 -- swift --version
"$ROOT/scripts/setup-linux.sh"
echo "Ready: run scripts/install-linux.sh to build and install agterm for your user."
