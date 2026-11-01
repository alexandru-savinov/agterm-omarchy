#!/usr/bin/env bash
# Build and launch the GTK frontend using Swift from PATH, SWIFT, or mise.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../linux/swift-env.sh
source "$ROOT/linux/swift-env.sh"
export AGTERM_GHOSTTY_RESOURCES="$ROOT/agterm-linux/vendor/ghostty/share/ghostty"
export AGTERM_ZMX_PATH="$ROOT/agterm-linux/vendor/zmx/zmx"
cd "$ROOT/agterm-linux"
"${SWIFT_COMMAND[@]}" build "$@"
BIN_DIR="$("${SWIFT_COMMAND[@]}" build "$@" --show-bin-path)"
exec "$BIN_DIR/AgtermLinux"
