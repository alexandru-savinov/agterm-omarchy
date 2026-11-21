#!/usr/bin/env bash
# Prepare the one fixed synthetic source locally. This job holds no sync credential.
set -euo pipefail

if [[ "${GITHUB_REPOSITORY:-}" != alexandru-savinov/agterm-omarchy ||
      "${GITHUB_EVENT_NAME:-}" != workflow_dispatch ||
      "${GITHUB_REF:-}" != refs/heads/main ||
      "${DRY_RUN:-}" != false || -n "${TAG_OVERRIDE:-}" ]]; then
  echo "::error::dispatch a rehearsal from collaboration main with dry_run=false and no tag" >&2
  exit 1
fi
if [[ ! "$GITHUB_RUN_ID" =~ ^[0-9]+$ || ! "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ||
      "$(git rev-parse HEAD)" != "$GITHUB_SHA" || -n "$(git status --porcelain)" ]]; then
  echo "::error::invalid rehearsal run identity or checkout" >&2
  exit 1
fi

# The fixed source ref exists only in this checkout. Only the per-attempt head is bundled.
fixture=rehearsal/linux-port-fixture
branch="rehearsal/linux-port-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
name=upstream-sync-rehearsal.txt
if [[ -e "$name" || -L "$name" ]]; then
  echo "::error::fixture file already exists on main" >&2
  exit 1
fi
git switch --quiet --create "$fixture"
printf '%s\n' 'Synthetic upstream sync publish rehearsal. Never merge.' > "$name"
git add -- "$name"
git -c user.name='Upstream sync rehearsal' -c user.email='upstream-sync-rehearsal@users.noreply.github.com' \
  commit --quiet -m 'Synthetic upstream sync publish rehearsal'
head="$(git rev-parse "refs/heads/$fixture")"
git branch "$branch" "$head"
mkdir "$RUNNER_TEMP/out"
git bundle create "$RUNNER_TEMP/out/sync.bundle" "refs/heads/$branch" "^$GITHUB_SHA"
cat > "$RUNNER_TEMP/out/body.md" <<'BODY'
Task #476

**Never merge.** This synthetic draft exercises the live upstream-sync publisher.
The rehearsal run closes the draft and deletes its temporary branch.
BODY
printf 'branch=%s\nhead=%s\n' "$branch" "$head" >> "$GITHUB_OUTPUT"
