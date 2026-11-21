#!/usr/bin/env bash
# The publish step of upstream-sync.yml: push the built sync branch and open its labelled PR, all as the PAT.
# Both land or neither stays: a stray branch fails every later run, and an open unlabelled PR blocks them
# without the labeled event that starts the sync's workflows.
set -euo pipefail

mode="${1:-publish}"
rehearsal="${REHEARSAL:-false}"
if [[ "$mode" != publish && "$mode" != --finish-rehearsal ]]; then
  echo "::error::unknown publish mode" >&2
  exit 1
fi
if [[ "$rehearsal" == true || "$mode" == --finish-rehearsal ]]; then
  if [[ "$rehearsal" != true || "${GH_REPO:-}" != alexandru-savinov/agterm-omarchy ||
        "${GITHUB_REF:-}" != refs/heads/main || "${GITHUB_EVENT_NAME:-}" != workflow_dispatch ||
        ! "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ || ! "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ||
        "${BRANCH:-}" != "rehearsal/linux-port-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" ||
        ! "${HEAD_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "::error::invalid rehearsal identity" >&2
    exit 1
  fi
  ownership="${RUNNER_TEMP:?}/upstream-sync-rehearsal-owned"
  if [[ "$mode" == publish ]] &&
     [[ "${DRAFT:-}" != true || "${LABEL:-}" != sync-rehearsal || "${TITLE:-}" != '[rehearsal]'* ]]; then
    echo "::error::invalid rehearsal draft metadata" >&2
    exit 1
  fi
fi
required=(SYNC_TOKEN GH_REPO BRANCH HEAD_SHA)
[[ "$mode" != publish ]] || required+=(TITLE DRAFT LABEL BODY_FILE)
for name in "${required[@]}"; do
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

delete_branch() {
  git push --force-with-lease="refs/heads/$BRANCH:$HEAD_SHA" origin ":refs/heads/$BRANCH"
}

finish_rehearsal() {
  if [[ ! -f "$ownership" || "$(< "$ownership")" != "$HEAD_SHA" ]]; then
    echo "::error::no ownership record for $BRANCH" >&2
    return 1
  fi
  local status=0 pr="${REHEARSAL_PR:-}" snapshot refs
  # Refuse to close a PR whose branch has already been moved by another actor.
  refs="$(git ls-remote --refs origin "refs/heads/$BRANCH")" || return 1
  if [[ -n "$refs" && "$refs" != "$(printf '%s\trefs/heads/%s' "$HEAD_SHA" "$BRANCH")" ]]; then
    echo "::error::rehearsal branch $BRANCH changed; leaving it and the PR alone" >&2
    return 1
  fi
  if [[ -z "$pr" ]]; then
    pr="$(this_pr)" || status=1
  fi
  if [[ "$pr" =~ ^[0-9]+$ ]] &&
     snapshot="$(gh pr view "$pr" --json headRefName,headRefOid,isCrossRepository,isDraft,state)" &&
     jq -e --arg branch "$BRANCH" --arg head "$HEAD_SHA" \
        '.isCrossRepository == false and .headRefName == $branch and .headRefOid == $head' \
        <<< "$snapshot" > /dev/null; then
    if ! jq -e '.isDraft == true and (.state == "OPEN" or .state == "CLOSED")' <<< "$snapshot" > /dev/null; then
      echo "::error::rehearsal #$pr is not an open or closed draft" >&2
      status=1
    fi
    if jq -e '.state == "OPEN"' <<< "$snapshot" > /dev/null; then
      gh pr close "$pr" --comment 'Publish rehearsal finished. This synthetic draft must never be merged.' || status=1
    fi
  else
    echo "::error::could not confirm the rehearsal PR for $BRANCH" >&2
    status=1
  fi
  # The regular failure handler may already have withdrawn the branch.
  if [[ -n "$refs" ]]; then
    delete_branch || status=1
  fi
  return "$status"
}

if [[ "$mode" == --finish-rehearsal ]]; then
  finish_rehearsal
  exit $?
fi

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
  local status=$?
  [[ -n "$pushed" && -z "$published" ]] || return 0
  if [[ -z "$number" ]]; then
    echo "::error::no PR from $BRANCH was found to close; deleting $BRANCH closes any this run opened" >&2
  elif ! gh pr close "$number" --comment "upstream-sync could not label this PR, so it withdrew it; the next run reopens the sync."; then
    echo "::error::could not close #$number; deleting $BRANCH closes it" >&2
  fi
  # GitHub closes an open PR whose head branch is deleted, so the delete stands in for a close that failed.
  delete_branch || echo "::error::could not delete $BRANCH; delete it and close any PR from it by hand" >&2
  exit $(( status ? status : 1 ))
}
trap withdraw EXIT

# The empty expected value creates the branch only, never moving one pushed since the plan. A ref already at
# HEAD_SHA also succeeds, as "[up to date]": only "[new branch]" makes the branch this run's to delete.
refspec="refs/heads/$BRANCH:refs/heads/$BRANCH"
pushout="$(git push --porcelain --force-with-lease="refs/heads/$BRANCH:" origin "$refspec")" \
  || { printf '%s\n' "$pushout"; echo "::error::could not create $BRANCH; see the push output above" >&2; exit 1; }
printf '%s\n' "$pushout"
grep -qxF "$(printf '*\t%s\t[new branch]' "$refspec")" <<< "$pushout" \
  || { echo "::error::$BRANCH already existed at $HEAD_SHA; this run created nothing and deletes nothing" >&2; exit 1; }
pushed=1
if [[ "$rehearsal" == true ]]; then
  printf '%s\n' "$HEAD_SHA" > "$ownership"
fi
as_draft=()
[[ "$DRAFT" != true ]] || as_draft=(--draft)
# gh pr create applies --label in a second call once the PR exists, so its failure can leave the PR unlabelled.
if url="$(gh pr create --base main --head "$BRANCH" --title "$TITLE" --label "$LABEL" \
    --body-file "$BODY_FILE" "${as_draft[@]}")"; then
  number="${url##*/}"
else
  number="$(this_pr)" || { echo "::error::gh pr create failed and the PR lookup for $BRANCH failed" >&2; exit 1; }
  [[ -n "$number" ]] || { echo "::error::gh pr create failed and no PR for $BRANCH is open" >&2; exit 1; }
  ensure_label "$number" || { echo "::error::could not apply $LABEL to #$number" >&2; exit 1; }
  echo "::warning::gh pr create failed after opening #$number; $LABEL was applied separately" >&2
fi
published=1
echo "number=$number" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "- opened: https://github.com/$GH_REPO/pull/$number" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
