#!/usr/bin/env bash
# Run the advisory hosted macOS gates in a disposable worktree of a PR merge commit.
set -euo pipefail

repo=alexandru-savinov/agterm-omarchy
url="https://github.com/$repo"
if [[ $# != 2 || ! ${1:-} =~ ^[1-9][0-9]*$ || ! ${2:-} =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: $0 <PR number> <GitHub Actions merge SHA>" >&2
    exit 2
fi
pr=$1
commit=$2
merge_ref="refs/pull/$pr/merge"

# The operator verifies the exported copy's digest against the reviewed pilot commit.
reviewed_dir="$HOME/Documents/orchestrator/reviews/conditional-ci-2026-11-27"
script_dir=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ -L ${BASH_SOURCE[0]} || ${BASH_SOURCE[0]##*/} != local-check.sh ||
      $script_dir != "$(cd -P -- "$reviewed_dir" 2>/dev/null && pwd -P)" ]] ||
    git rev-parse --show-toplevel >/dev/null 2>&1 ||
    git -C "$script_dir" rev-parse --show-toplevel >/dev/null 2>&1; then
    echo 'Run the reviewed copy from a directory outside any Git worktree.' >&2
    exit 2
fi

if [[ $(uname -s) != Darwin ]]; then
    echo 'This check requires macOS.' >&2
    exit 2
fi
if [[ $(gh api --hostname github.com user --jq .login) != alexandru-savinov ]]; then
    echo 'The active GitHub login must be alexandru-savinov.' >&2
    exit 2
fi
owner_pr() {
    local number
    number=$(gh api --hostname github.com "repos/$repo/pulls/$pr" --jq '
        select(.state == "open" and .user.login == "alexandru-savinov" and
               .head.repo.full_name == "alexandru-savinov/agterm-omarchy") | .number') &&
        [[ $number == "$pr" ]]
}
if ! owner_pr; then
    echo 'Local validation requires an open, owner-authored PR with its head in alexandru-savinov/agterm-omarchy.' >&2
    exit 2
fi
if [[ $(dscl . -read "/Users/$(id -un)" UserShell) != 'UserShell: /bin/zsh' ]]; then
    echo 'The hosted check requires a zsh login shell.' >&2
    exit 2
fi

temp_root=$(cd -P -- "${TMPDIR:-/tmp}" && pwd -P)
scratch=$(mktemp -d "${temp_root%/}/agterm-local-check.XXXXXXXX")
worktree="$scratch/worktree"
log="$scratch/validation.log"
echo "Validation log will be saved in $log"

# Use separate Git metadata and disable checkout hooks.
git init --bare --quiet "$scratch/git"
repo_git() {
    git --git-dir="$scratch/git" -c core.hooksPath=/dev/null "$@"
}
repo_git fetch --quiet --depth=1 --no-tags "$url.git" "$merge_ref"
if [[ $(repo_git rev-parse FETCH_HEAD) != "$commit" ]]; then
    echo 'GitHub no longer exposes the requested PR merge commit.' >&2
    exit 2
fi
repo_git worktree add --quiet --detach "$worktree" "$commit"
# Setup and build scripts may clean build artifacts only in this disposable checkout.
if [[ $worktree != "${temp_root%/}/"* ]] ||
   ! cd -P -- "$worktree" || [[ $(pwd -P) != "$worktree" ]]; then
    echo 'Refusing to run setup or make: the worktree resolves outside the temporary root or its allocated checkout.' >&2
    exit 2
fi
trap '(cd -P -- "$scratch" && repo_git worktree remove --force "$worktree")' EXIT
tree=$(git -C "$worktree" rev-parse 'HEAD^{tree}')
if [[ -n $(git -C "$worktree" status --porcelain --untracked-files=all) ]]; then
    echo 'The fetched worktree is dirty.' >&2
    exit 2
fi

mkdir "$scratch/home" "$worktree/.tmp"
run_isolated() {
    # Foundation tools can resolve the account home independently of HOME.
    # nix-darwin's zshenv must preserve the PATH already supplied to the isolated run.
    env -i PATH="$PATH" HOME="$scratch/home" CFFIXED_USER_HOME="$scratch/home" TMPDIR="$worktree/.tmp" \
        __NIX_DARWIN_SET_ENVIRONMENT_DONE=1 \
        USER="$(id -un)" LOGNAME="$(id -un)" SHELL=/bin/zsh \
        DEVELOPER_DIR="${DEVELOPER_DIR:-$(xcode-select -p)}" "$@"
}
versions="$scratch/versions.json"
run_isolated python3 -I - "$versions" <<'PY'
import json
import platform
import subprocess
import sys


def version(*command):
    return subprocess.check_output(command, text=True).strip()


node = version("node", "--version")
major, minor, *_ = map(int, node.removeprefix("v").split("."))
if major != 22 or minor < 7:
    raise SystemExit("The hosted check requires Node 22.7 or later in the Node 22 series.")

versions = {
    "macOS": version("sw_vers"),
    "Xcode": version("xcodebuild", "-version"),
    "tools": "\n".join((
        platform.machine(), version("swift", "--version"),
        version("xcodegen", "--version"), "SwiftLint " + version("swiftlint", "version"),
        version("fish", "--version"), "Node " + node, version("make", "--version").splitlines()[0],
    )),
}
with open(sys.argv[1], "w") as output:
    json.dump(versions, output)
print(json.dumps(versions, indent=2))
PY

printf 'commit=%s tree=%s\n' "$commit" "$tree" > "$log"
cat "$versions" >> "$log"
printf '\n' >> "$log"
exit_code=0
(
    run_isolated /bin/bash -se <<'CHECK'
scripts/setup.sh
result=0
make build || result=$?
AGTERM_CONTROL_SOCKET="$TMPDIR/no-app.sock" AGTERM_REQUIRE_FISH=1 AGTERM_REQUIRE_ZMX=1 make test || result=$?
TEST_RUNNER_AGTERM_REQUIRE_FISH=1 make test-app || result=$?
make lint || result=$?
exit "$result"
CHECK
) >> "$log" 2>&1 || exit_code=$?
current=$(git ls-remote "$url.git" "$merge_ref")
if [[ ${current%%$'\t'*} != "$commit" ]] || ! owner_pr; then
    echo 'The PR merge ref or eligibility changed during validation. No status was posted.' >&2
    exit 2
fi

# Status descriptions have a 140-character limit. Full versions live in the target URL fragment.
python3 -I - "$url" "$commit" "$tree" "$exit_code" "$versions" "$log" <<'PY' |
import hashlib
import json
import sys
from urllib.parse import quote

url, commit, tree, code, versions, log = sys.argv[1:]
with open(versions) as source:
    metadata = quote(json.dumps(json.load(source), separators=(",", ":")), safe="")
with open(log, "rb") as source:
    digest = hashlib.file_digest(source, "sha256").hexdigest()
print(json.dumps({
    "context": "local/macos-validation",
    "state": "success" if code == "0" else "failure",
    "description": f"v1 tree={tree} exit={code} sha256={digest}",
    "target_url": f"{url}/commit/{commit}#local-check={metadata}",
}))
PY
    gh api --hostname github.com --method POST "repos/$repo/statuses/$commit" --input - \
        --jq '{context,state,description,target_url}'
exit "$exit_code"
