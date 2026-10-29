#!/usr/bin/env bash
# Compile and run small programs without a checkout, network, or display.
set -euo pipefail
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export ZIG_GLOBAL_CACHE_DIR="$work/zig-cache"
export ZIG_LOCAL_CACHE_DIR="$work/zig-local-cache"

test "$(uname -m)" = x86_64
swift --version | tee "$work/swift-version"
grep -q 'Swift version 6.3.2' "$work/swift-version"
test "$(zig version)" = 0.16.0
test "$(swiftlint version)" = 0.65.0
pkg-config --modversion gtk4 libadwaita-1 webkitgtk-6.0 epoxy
for command in zsh Xvfb dbus-run-session xdotool shellcheck; do
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
    g_print("GTK %u; libadwaita %u; WebKit %u\n", gtk_get_major_version(),
            adw_get_major_version(), webkit_get_major_version());
    return 0;
}
C
# pkg-config emits compiler arguments intentionally split into words.
# shellcheck disable=SC2046
gcc "$work/probe.c" $(pkg-config --cflags --libs gtk4 libadwaita-1 webkitgtk-6.0 epoxy) -o "$work/gtk-probe"
"$work/gtk-probe"
echo 'PASS: pinned compilers, Foundation, GTK/libadwaita/WebKit linking and test dependencies'
