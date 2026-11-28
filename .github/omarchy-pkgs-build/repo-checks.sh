#!/bin/bash
# omarchy-pkgs' own recipe checks (#465), run as an unprivileged user in a
# throwaway Arch container on a writable checkout that already carries the
# recipe. Usage: repo-checks.sh OMARCHY_PKGS_DIR OWNERS_LINE_FILE OUTDIR
set -euo pipefail
pkgs=$1 owners_line=$2 out=$3
package=agterm-omarchy-bin
results="$out/repo-checks.tsv"
: >"$results"
failed=0
record() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" | tee -a "$results"; [[ $1 == PASS ]] || failed=1; }
run() {
  local name=$1; shift
  if "$@" >"$out/$name.txt" 2>&1; then record PASS "$name" "$(tail -1 "$out/$name.txt")"
  else record FAIL "$name" "exit $?: $(tail -1 "$out/$name.txt")"; fi
}

cd "$pkgs"
run pinned-sources tests/pinned-sources.sh
run pkgname-unique tests/pkgname-unique.sh
run check-shadowed bin/check-shadowed "$package"

bin/release-owners "$package" >"$out/release-owners.txt" 2>&1 || true
generated=$(git diff --unified=0 release/owners.json | sed -n 's/^+ {/ {/p')
if [[ "$generated" == "$(cat "$owners_line")" ]]; then
  record PASS release-owners "generated line matches packaging/omarchy-pkgs/owners.json.line"
else
  record FAIL release-owners "generated: ${generated:-nothing}"
fi
git add -A pkgbuilds release/owners.json
git -c user.name=check -c user.email=check@invalid commit -qm "agterm-omarchy-bin draft"
run release-owners-check bin/release-owners --check --base HEAD^1

if python helpers/upstream-watch.py check "pkgbuilds/$package" >"$out/upstream-watch.txt" 2>&1 \
  && grep -q '"already current"' "$out/upstream-watch.txt"; then
  record PASS upstream-watch "$(tail -1 "$out/upstream-watch.txt")"
else
  record FAIL upstream-watch "$(tail -1 "$out/upstream-watch.txt")"
fi
exit "$failed"
