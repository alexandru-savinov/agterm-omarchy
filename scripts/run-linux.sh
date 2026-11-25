#!/usr/bin/env bash
<<<<<<< HEAD
# Build and launch the GTK frontend using Swift from PATH, SWIFT, or mise.
=======
# Build and launch agterm-linux (the GTK4/libadwaita port).
# Requires the mise toolchain (zig 0.17.0 + swift 6.3.2) and a built libghostty
# in agterm-linux/vendor/ (run scripts/setup-linux.sh once).
>>>>>>> 6582f12240cd1dc4bc1c690bfdb2f7552f356227
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
# shellcheck source=../linux/swift-env.sh
source "$ROOT/linux/swift-env.sh"
export AGTERM_GHOSTTY_RESOURCES="$ROOT/agterm-linux/vendor/ghostty/share/ghostty"
export AGTERM_ZMX_PATH="$ROOT/agterm-linux/vendor/zmx/zmx"
cd "$ROOT/agterm-linux"
"${SWIFT_COMMAND[@]}" build "$@"
BIN_DIR="$("${SWIFT_COMMAND[@]}" build "$@" --show-bin-path)"
exec "$BIN_DIR/AgtermLinux"
