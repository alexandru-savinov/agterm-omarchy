#!/usr/bin/env bash
# Compile and run native programs and software-rendered display probes without a checkout or network.
set -euo pipefail
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export ZIG_GLOBAL_CACHE_DIR="$work/zig-cache"
export ZIG_LOCAL_CACHE_DIR="$work/zig-local-cache"

test "$(uname -m)" = aarch64
test "$(id -u)" = 1001
export HOME="$work/home" XDG_RUNTIME_DIR="$work/runtime"
mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
swift --version | tee "$work/swift-version"
grep -q 'Swift version 6.3.2' "$work/swift-version"
test "$(zig version)" = 0.16.0
pkg-config --modversion gtk4 libadwaita-1 webkitgtk-6.0 epoxy
for command in zsh Xvfb xvfb-run dbus-run-session xdotool openbox glxinfo shellcheck desktop-file-validate; do
  command -v "$command"
done
python3 -c 'import gi; gi.require_version("Atspi", "2.0"); from gi.repository import Atspi; print("AT-SPI bindings available")'

cat > "$work/probe.swift" <<'SWIFT'
import Foundation
print("Swift Foundation compiler probe: \(ProcessInfo.processInfo.operatingSystemVersionString)")
SWIFT
swiftc -module-cache-path "$work/swift-cache" "$work/probe.swift" -o "$work/swift-probe"
"$work/swift-probe"

printf 'pub fn main() void {}\n' > "$work/probe.zig"
zig build-exe "$work/probe.zig" -O ReleaseSafe -femit-bin="$work/zig-probe"
"$work/zig-probe"

cat > "$work/probe.c" <<'C'
#include <gtk/gtk.h>
#include <adwaita.h>
#include <webkit/webkit.h>
#include <epoxy/gl.h>
int main(void) {
    if (!gtk_init_check()) return 1;
    adw_init();
    g_print("GTK %u; libadwaita %u; WebKit %u\n", gtk_get_major_version(),
            adw_get_major_version(), webkit_get_major_version());
    return 0;
}
C
# pkg-config emits compiler arguments intentionally split into words.
# shellcheck disable=SC2046
gcc "$work/probe.c" $(pkg-config --cflags --libs gtk4 libadwaita-1 webkitgtk-6.0 epoxy) -o "$work/gtk-probe"
# The child shell expands its own positional argument.
# shellcheck disable=SC2016
LIBGL_ALWAYS_SOFTWARE=1 GDK_BACKEND=x11 xvfb-run -a dbus-run-session -- bash -euo pipefail -c '
  glxinfo -B | tee "$1/gl-info"
  grep -Eq "OpenGL renderer string:.*(llvmpipe|softpipe)" "$1/gl-info"
  "$1/gtk-probe"
' _ "$work"
echo 'PASS: native aarch64, non-root, pinned compilers, Foundation, GTK display/linking, software GL and test dependencies'
