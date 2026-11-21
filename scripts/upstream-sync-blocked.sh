#!/usr/bin/env bash
# The blocked path of upstream-sync.yml: a sync whose merge adds workflow files is never pushed. This records
# it in the one open issue kept for that, rewriting the issue when a later run is blocked on another head.
set -euo pipefail

for name in GH_TOKEN GH_REPO TITLE LABEL BODY_FILE; do
  [[ -n "${!name:-}" ]] || { echo "::error::$name is not set" >&2; exit 1; }
done
PREFIX='Upstream sync blocked: new workflow file(s) from '  # BLOCKED_TITLE in upstream-sync.py
[[ "$TITLE" == "$PREFIX"* ]] || { echo "::error::title does not start with '$PREFIX'" >&2; exit 1; }

# Listed, not searched: the search index lags, and a missed match opens a second issue. The label is what
# makes an issue ours, since only a maintainer can apply one.
listing="$(gh issue list --state open --label "$LABEL" --limit 1000 --json number,title \
  --jq 'sort_by(.number) | .[] | [.number, .title] | @tsv')"
number=''
while IFS=$'\t' read -r candidate heading; do
  [[ "$heading" == "$PREFIX"* ]] || continue
  number="$candidate"
  break
done <<< "$listing"

if [[ -n "$number" ]]; then
  gh issue edit "$number" --title "$TITLE" --body-file "$BODY_FILE" > /dev/null
else
  url="$(gh issue create --title "$TITLE" --label "$LABEL" --body-file "$BODY_FILE")"
  number="${url##*/}"
fi
echo "number=$number" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "- blocked: https://github.com/$GH_REPO/issues/$number" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "::warning::$TITLE; nothing was pushed, see https://github.com/$GH_REPO/issues/$number"
