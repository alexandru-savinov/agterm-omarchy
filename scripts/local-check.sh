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

# BEGIN local path guard
require_canonical_paths() {
    python3 -I - "$@" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
scan = sys.argv[2] == "scan"


def refuse(path, reason):
    raise SystemExit(f"Refusing local validation: {path}: {reason}")


def resolve(path):
    try:
        return path.resolve()
    except (OSError, RuntimeError) as error:
        refuse(path, str(error))


if not root.is_absolute() or resolve(root) != root or not root.is_dir():
    refuse(root, "expected a physical directory")
for value in sys.argv[3:]:
    path = Path(value)
    if root not in path.parents or resolve(path) != path:
        refuse(path, f"expected a physical path below {root}")


def scan_error(error):
    refuse(error.filename, str(error))


if scan:
    for parent, directories, files in os.walk(root, onerror=scan_error):
        for name in directories + files:
            path = Path(parent, name)
            if path.is_symlink() and not resolve(path).is_relative_to(root):
                refuse(path, f"symlink resolves outside {root}")
PY
    local result=$?
    if (( result != 0 )); then
        return 2
    fi
}

guard_gate_paths() {
    guard_scratch_paths || return 2
    require_canonical_paths "$worktree" scan \
        "$isolated_home" "$swiftpm_cache" "$zig_cache" \
        "$build_tmp" "$build_tmp/agterm-setup.XXXXXXXX" "$worktree/build/DerivedData" \
        "$worktree/agtermCore/.build" "$worktree/agterm.xcodeproj" \
        "$worktree/GhosttyKit.xcframework" "$worktree/agterm/Resources/ghostty" \
        "$worktree/agterm/Resources/terminfo" "$worktree/agterm/Resources/zmx" \
        "$worktree/.ghostty-build-stamp" "$worktree/.zmx-build-stamp"
}

guard_scratch_paths() {
    require_canonical_paths "$temp_root" paths \
        "$scratch" "$worktree" "$scratch/git" "$log" "$versions"
}

cleanup_worktree() {
    guard_scratch_paths || return 2
    (cd -P -- "$scratch" && repo_git worktree remove --force "$worktree")
}
# END local path guard

temp_root=$(cd -P -- "${TMPDIR:-/tmp}" && pwd -P)
scratch=$(mktemp -d "${temp_root%/}/agterm-local-check.XXXXXXXX")
if [[ $scratch != "${temp_root%/}/agterm-local-check."* ||
      ${scratch%/*} != "${temp_root%/}" || ! -d $scratch ]]; then
    echo "Refusing local validation: unexpected scratch allocation $scratch" >&2
    exit 2
fi
worktree="$scratch/worktree"
log="$scratch/validation.log"
versions="$scratch/versions.json"
guard_scratch_paths || exit 2
echo "Validation log will be saved in $log"

# Keep inherited Git path overrides and checkout hooks out of the disposable repository.
isolated_git() {
    env -i PATH="$PATH" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git "$@"
}
isolated_git init --bare --quiet "$scratch/git"
repo_git() {
    isolated_git --git-dir="$scratch/git" -c core.hooksPath=/dev/null "$@"
}
repo_git fetch --quiet --depth=1 --no-tags "$url.git" "$merge_ref"
if [[ $(repo_git rev-parse FETCH_HEAD) != "$commit" ]]; then
    echo 'GitHub no longer exposes the requested PR merge commit.' >&2
    exit 2
fi
guard_scratch_paths || exit 2
repo_git worktree add --quiet --detach "$worktree" "$commit"
guard_scratch_paths || exit 2
cd -P -- "$worktree"
trap 'cleanup_worktree || exit 2' EXIT
tree=$(isolated_git -C "$worktree" rev-parse 'HEAD^{tree}')
if [[ -n $(isolated_git -C "$worktree" status --porcelain --untracked-files=all) ]]; then
    echo 'The fetched worktree is dirty.' >&2
    exit 2
fi

# Keep Swift macro sources and isolated home data under lint's existing build exclusion.
build_tmp="$worktree/build/.tmp"
isolated_home="$worktree/build/.local-check-home"
swiftpm_cache="$isolated_home/Library/Caches/org.swift.swiftpm"
zig_cache="$isolated_home/.cache/zig"
guard_gate_paths || exit 2
if ! grep -Fxq '# Supports AGTERM_LOCAL_CHECK_ROOT from scripts/local-check.sh.' scripts/setup.sh; then
    echo 'Refusing local validation: the merge lacks isolated setup support.' >&2
    exit 2
fi
mkdir -p -- "$build_tmp" "$swiftpm_cache" "$zig_cache"
run_isolated() {
    # Foundation tools can resolve the account home independently of HOME.
    # nix-darwin's zshenv must preserve the PATH already supplied to the isolated run.
    env -i PATH="$PATH" HOME="$isolated_home" CFFIXED_USER_HOME="$isolated_home" TMPDIR="$build_tmp" \
        ZIG_GLOBAL_CACHE_DIR="$zig_cache" AGTERM_LOCAL_CHECK_ROOT="$worktree" \
        __NIX_DARWIN_SET_ENVIRONMENT_DONE=1 \
        TEST_RUNNER_HOME="$isolated_home" TEST_RUNNER_CFFIXED_USER_HOME="$isolated_home" TEST_RUNNER_TMPDIR="$build_tmp" \
        TEST_RUNNER___CF_USER_TEXT_ENCODING="${__CF_USER_TEXT_ENCODING:-$(printf '0x%X:0:0' "$(id -u)")}" \
        USER="$(id -un)" LOGNAME="$(id -un)" SHELL=/bin/zsh \
        DEVELOPER_DIR="${DEVELOPER_DIR:-$(xcode-select -p)}" "$@"
}
guard_gate_paths || exit 2
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

guard_gate_paths || exit 2
printf 'commit=%s tree=%s\n' "$commit" "$tree" > "$log"
cat "$versions" >> "$log"
printf '\n' >> "$log"
exit_code=0
run_isolated scripts/setup.sh >> "$log" 2>&1 || exit_code=$?
if (( exit_code == 0 )); then
    guard_gate_paths || exit 2
    run_isolated make build >> "$log" 2>&1 || exit_code=$?
    guard_gate_paths || exit 2
    run_isolated env AGTERM_CONTROL_SOCKET="$build_tmp/no-app.sock" AGTERM_REQUIRE_FISH=1 AGTERM_REQUIRE_ZMX=1 \
        make test >> "$log" 2>&1 || exit_code=$?
    guard_gate_paths || exit 2
    run_isolated env TEST_RUNNER_AGTERM_REQUIRE_FISH=1 make test-app >> "$log" 2>&1 || exit_code=$?
    guard_gate_paths || exit 2
    run_isolated make lint >> "$log" 2>&1 || exit_code=$?
fi
guard_gate_paths || exit 2
cd -P -- "$scratch"
trap - EXIT
cleanup_worktree || exit 2
guard_scratch_paths || exit 2
current=$(isolated_git ls-remote "$url.git" "$merge_ref")
if [[ ${current%%$'\t'*} != "$commit" ]] || ! owner_pr; then
    echo 'The PR merge ref or eligibility changed during validation. No status was posted.' >&2
    exit 2
fi

# Status descriptions have a 140-character limit. Full versions live in the target URL fragment.
# Keep the pilot ineligible for a skip until the supervised Live pane encoding bug is fixed.
if (( exit_code == 0 )); then
    echo 'Local macOS validation is held by the known supervised Live pane text-encoding failures.' | tee -a "$log" >&2
    exit_code=1
fi
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
