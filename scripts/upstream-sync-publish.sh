#!/usr/bin/env bash
# The publish step of upstream-sync.yml: push the built sync branch and open its labelled PR, all as the PAT.
# Both land or neither stays: a stray branch fails every later run, and an open unlabelled PR blocks them
# without the labeled event that starts the sync's workflows.
set -euo pipefail

for name in SYNC_TOKEN GH_REPO BRANCH HEAD_SHA TITLE DRAFT LABEL BODY_FILE; do
  [[ -n "${!name:-}" ]] || { echo "::error::$name is not set" >&2; exit 1; }
done
LABEL_ATTEMPTS="${LABEL_ATTEMPTS:-3}"
LABEL_RETRY_DELAY="${LABEL_RETRY_DELAY:-5}"

auth="$(printf 'x-access-token:%s' "$SYNC_TOKEN" | base64 | tr -d '\n')"
echo "::add-mask::$auth"
# Through the environment, so the credential is in no argv and no persisted git config.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
export GH_TOKEN="$SYNC_TOKEN"

pushed='' published='' number=''

this_pr() {
  gh pr list --head "$BRANCH" --state open --json number,headRefOid,isCrossRepository \
    --jq "[.[] | select((.isCrossRepository | not) and .headRefOid == \"$HEAD_SHA\")][0].number // empty"
}

has_label() {
  gh api "repos/$GH_REPO/issues/$1/labels" --jq '.[].name' | grep -qxF "$LABEL"
}

ensure_label() {
  local attempt
  for ((attempt = 1; attempt <= LABEL_ATTEMPTS; attempt++)); do
    has_label "$1" && return 0
    gh api "repos/$GH_REPO/issues/$1/labels" -f "labels[]=$LABEL" > /dev/null && has_label "$1" && return 0
    sleep "$LABEL_RETRY_DELAY"
  done
  return 1
}

withdraw() {
  [[ -n "$pushed" && -z "$published" ]] || return 0
  if [[ -n "$number" ]]; then
    gh pr close "$number" --comment "upstream-sync could not label this PR, so it withdrew it; the next run reopens the sync." \
      || echo "::error::could not close #$number" >&2
  fi
  # Deleting the head also closes a PR this run opened but could not find.
  git push --force-with-lease="refs/heads/$BRANCH:$HEAD_SHA" origin ":refs/heads/$BRANCH" \
    || echo "::error::could not delete $BRANCH; delete it by hand" >&2
}
trap withdraw EXIT

# The empty expected value creates the branch only, never moving one pushed since the plan.
git push --force-with-lease="refs/heads/$BRANCH:" origin "refs/heads/$BRANCH:refs/heads/$BRANCH"
pushed=1
draft=()
[[ "$DRAFT" != true ]] || draft=(--draft)
# gh pr create applies --label in a second call once the PR exists, so its failure can leave the PR unlabelled.
if url="$(gh pr create --base main --head "$BRANCH" --title "$TITLE" --label "$LABEL" \
    --body-file "$BODY_FILE" "${draft[@]}")"; then
  number="${url##*/}"
else
  number="$(this_pr)" || number=''
  [[ -n "$number" ]] || { echo "::error::gh pr create failed and no PR for $BRANCH is open" >&2; exit 1; }
  ensure_label "$number" || { echo "::error::could not apply $LABEL to #$number" >&2; exit 1; }
  echo "::warning::gh pr create failed after opening #$number; $LABEL was applied separately" >&2
fi
published=1
echo "number=$number" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "- opened: https://github.com/$GH_REPO/pull/$number" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
