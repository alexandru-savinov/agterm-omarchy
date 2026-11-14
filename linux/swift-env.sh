# shellcheck shell=bash
# shellcheck disable=SC2034 # SWIFT_COMMAND is consumed by sourcing build helpers.
# Source from Linux build helpers; compatibility libraries are prepared by setup-omarchy.sh.
SWIFT_COMPAT="${XDG_DATA_HOME:-$HOME/.local/share}/agterm/swift-compat"
if [[ -d "$SWIFT_COMPAT" ]]; then
  export LD_LIBRARY_PATH="$SWIFT_COMPAT${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# A PATH swift wins only at the pinned version; SWIFT= is the explicit override for any other toolchain.
path_swift_version=""
if command -v swift >/dev/null 2>&1 && [[ "$(swift --version 2>/dev/null || true)" =~ Swift\ version\ ([0-9.]+) ]]; then
  path_swift_version="${BASH_REMATCH[1]}"
fi
if [[ -n "${SWIFT:-}" ]]; then
  SWIFT_COMMAND=("$SWIFT")
elif [[ "$path_swift_version" == 6.3.2 ]]; then
  SWIFT_COMMAND=(swift)
elif command -v mise >/dev/null 2>&1; then
  SWIFT_COMMAND=(mise exec swift@6.3.2 -- swift)
elif command -v swift >/dev/null 2>&1; then
  echo "PATH swift is ${path_swift_version:-an unknown version}, not 6.3.2; install mise or set SWIFT=<path>" >&2
  return 1
else
  echo "Swift is required; add it to PATH or run scripts/setup-omarchy.sh" >&2
  return 1
fi
