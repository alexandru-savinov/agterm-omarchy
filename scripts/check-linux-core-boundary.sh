#!/usr/bin/env bash
# Enforce the host-free agtermCore boundary and show the portable downstream delta.
# Usage: scripts/check-linux-core-boundary.sh [UPSTREAM_BASE]
set -euo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
BASE="${1:-v0.32.0}"
CORE="$ROOT/agtermCore/Sources/agtermCore"
PROTECTED_TEST="agtermCore/Tests/agtermCoreTests/ConfigPathsTests.swift"

git -C "$ROOT" rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null || {
  echo "unknown upstream base: $BASE" >&2
  exit 2
}

# ripgrep status 1 means no matches; every other nonzero status is a scan failure.
# Keep search and filter stages separate so a pipeline cannot hide a failed scan.
checked_rg() {
  local status
  if rg "$@"; then
    return 0
  else
    status=$?
  fi
  if [[ "$status" -eq 1 ]]; then
    return 0
  fi
  echo "core boundary search failed (rg exit $status)" >&2
  return 2
}

# Permit only one reviewed byte-for-byte fixture delta; see .github/CI.md.
reviewed_fixture_delta() {
  local exceptions="$ROOT/packaging/linux/core-fixture-exceptions.tsv"
  local row path base_blob fixture_blob rationale extra upstream_blob current_blob
  local records=0 matched=1
  [[ -f "$exceptions" && ! -L "$exceptions" ]] || return 1
  [[ -f "$ROOT/$PROTECTED_TEST" && ! -L "$ROOT/$PROTECTED_TEST" && ! -x "$ROOT/$PROTECTED_TEST" ]] || return 1
  upstream_blob="$(git -C "$ROOT" rev-parse "${BASE}:${PROTECTED_TEST}")" || return 2
  current_blob="$(git -C "$ROOT" hash-object --no-filters -- "$ROOT/$PROTECTED_TEST")" || return 2
  while IFS= read -r row || [[ -n "$row" ]]; do
    [[ -z "$row" || "$row" == \#* ]] && continue
    IFS=$'\t' read -r path base_blob fixture_blob rationale extra <<< "$row"
    records=$((records + 1))
    if [[ "$records" -ne 1 || "$path" != "$PROTECTED_TEST" ||
          ! "$base_blob" =~ ^[0-9a-f]{40}$ || ! "$fixture_blob" =~ ^[0-9a-f]{40}$ ||
          ! "$rationale" =~ ^https://github\.com/alexandru-savinov/agterm-omarchy/issues/[1-9][0-9]*$ ||
          -n "$extra" || "$row" != "$path"$'\t'"$base_blob"$'\t'"$fixture_blob"$'\t'"$rationale" ]]; then
      echo "malformed protected-fixture exception: $exceptions" >&2
      return 2
    fi
    if [[ "$base_blob" == "$upstream_blob" && "$fixture_blob" == "$current_blob" ]]; then
      matched=0
    fi
  done < "$exceptions"
  return "$matched"
}

failed=0
imports="$(checked_rg -n '^[[:space:]]*import[[:space:]]+(AppKit|SwiftUI|Metal|MetalKit|GhosttyKit|Glibc|CoreGraphics|CGtk|Gtk|Adwaita)([[:space:]]|$)' "$CORE")"
if [[ -n "$imports" ]]; then
  printf '%s\n' "$imports"
  echo "agtermCore imports a prohibited host module" >&2
  failed=1
fi

host_symbols="$(checked_rg -n '\b(ghostty|gtk|adw)_[[:alnum:]_]+' "$CORE")"
host_symbols="$(checked_rg -v '^[^:]+:[0-9]+:[[:space:]]*//' <<< "$host_symbols")"
if [[ -n "$host_symbols" ]]; then
  printf '%s\n' "$host_symbols"
  echo "agtermCore references a prohibited host API symbol" >&2
  failed=1
fi

if git -C "$ROOT" diff --quiet "$BASE" -- "$PROTECTED_TEST"; then
  :
else
  status=$?
  [[ "$status" -eq 1 ]] || exit 2
  if reviewed_fixture_delta; then
    echo "$PROTECTED_TEST matches its exact reviewed fixture exception"
  else
    status=$?
    [[ "$status" -eq 1 ]] || exit 2
    echo "$PROTECTED_TEST differs from $BASE" >&2
    git -C "$ROOT" diff "$BASE" -- "$PROTECTED_TEST" >&2
    failed=1
  fi
fi

echo "Portable agtermCore delta from $BASE:"
delta="$(git -C "$ROOT" diff --name-status "$BASE" -- agtermCore/Package.swift agtermCore/Sources agtermCore/Tests)"
if [[ -n "$delta" ]]; then
  printf '%s\n' "$delta"
else
  echo "(none)"
fi

exit "$failed"
