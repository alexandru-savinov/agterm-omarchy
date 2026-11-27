#!/usr/bin/env python3
"""Exercise wrapper refusals without running the macOS gates or writing a status."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[2] / "scripts/local-check.sh"


class LocalCheck(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "pr-worktree"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.reviewed = self.root / "Documents/orchestrator/reviews/conditional-ci-2026-11-27/local-check.sh"
        self.reviewed.parent.mkdir(parents=True)
        shutil.copyfile(WRAPPER, self.reviewed)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "commands"
        self.env = {**os.environ, "HOME": str(self.root), "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
                    "TMPDIR": str(self.root), "CALLS": str(self.log)}
        stubs = {
            "uname": "echo Darwin",
            "dscl": '''echo 'UserShell: /bin/zsh' ''',
            "gh": '''
printf '%s\\n' "$*" >> "$CALLS"
case " $* " in
  *" user "*) echo alexandru-savinov ;;
  *"/pulls/77 "*) jq -r "${@: -1}" <<< "$PR_JSON" ;;
  *) exit 99 ;;
esac''',
            "mktemp": '''echo mktemp >> "$CALLS"; exit 99''',
        }
        for name, script in stubs.items():
            self.stub(name, script)

    def stub(self, name, script):
        path = self.bin_dir / name
        path.write_text("#!/bin/bash\n" + script + "\n")
        path.chmod(0o755)

    def run_wrapper(self, path, cwd, **env):
        return subprocess.run(["bash", str(path), "77", "a" * 40], text=True, capture_output=True,
                              env={**self.env, **env}, cwd=cwd)

    def test_unreviewed_copy_and_pr_worktree_are_refused_before_api_access(self):
        for path, cwd in ((WRAPPER, self.root), (self.reviewed, self.repo)):
            with self.subTest(path=path, cwd=cwd):
                attempt = self.run_wrapper(path, cwd)
                self.assertEqual(attempt.returncode, 2, attempt)
                self.assertIn("reviewed copy", attempt.stderr)
                self.assertFalse(self.log.exists())

    @unittest.skipUnless(shutil.which("jq"), "fixture evaluates the PR metadata filter with jq")
    def test_ineligible_pr_is_refused_before_creating_scratch(self):
        repo = "alexandru-savinov/agterm-omarchy"
        owner = "alexandru-savinov"
        for head, author in (("other/agterm-omarchy", owner), (repo, "another-author"), (None, owner)):
            with self.subTest(head=head, author=author):
                payload = {"number": 77, "state": "open", "head": {"repo": {"full_name": head} if head else None},
                           "user": {"login": author}}
                attempt = self.run_wrapper(self.reviewed, self.root, PR_JSON=json.dumps(payload))
                self.assertEqual(attempt.returncode, 2, attempt)
                self.assertIn("owner-authored", attempt.stderr)
                calls = self.log.read_text()
                self.assertIn("/pulls/77", calls)
                self.assertNotIn("mktemp", calls)

    @unittest.skipUnless(shutil.which("jq"), "fixture evaluates the PR metadata filter with jq")
    def test_only_physical_worktree_can_run_the_gates(self):
        owner = "alexandru-savinov"
        payload = {"number": 77, "state": "open", "user": {"login": owner},
                   "head": {"repo": {"full_name": f"{owner}/agterm-omarchy"}}}
        temp_root = self.root / "temp-root"
        temp_root.mkdir()
        alias = self.root / "temp-alias"
        alias.symlink_to(temp_root, target_is_directory=True)
        outside = self.root / "temp-root-outside"
        outside.mkdir()
        self.stub("mktemp", 'exec /usr/bin/mktemp "$@"')
        self.stub("git", '''
case " $* " in
  *" worktree add "*)
    worktree=${@: -2:1}
    if [[ $ESCAPE == yes ]]; then
        ln -s "$OUTSIDE" "$worktree"
    else
        mkdir "$worktree"
    fi
    mkdir "$worktree/scripts"
    cp "$FIXTURE" "$worktree/scripts/setup.sh"
    ;;
  *" rev-parse FETCH_HEAD "*|*" rev-parse HEAD^{tree} "*)
    printf '%040d\n' 0 | tr 0 a
    ;;
  *" rev-parse --show-toplevel "*) exit 1 ;;
  *" ls-remote "*) echo replaced-merge ;;
esac''')
        self.stub("xcode-select", "echo /fixture/Developer")
        self.stub("python3", 'cat >/dev/null; echo "{}" > "$3"')
        self.stub("make", '''
printf "make %s\n" "$*"
if [[ $1 == test ]]; then
    printf "test socket=%s nix environment initialized=%s\n" \
        "$AGTERM_CONTROL_SOCKET" "$__NIX_DARWIN_SET_ENVIRONMENT_DONE"
fi
if [[ $1 == test-app ]]; then
    printf "XCTest HOME=%s CFFIXED_USER_HOME=%s encoding=%s\n" \
        "$TEST_RUNNER_HOME" "$TEST_RUNNER_CFFIXED_USER_HOME" "$TEST_RUNNER___CF_USER_TEXT_ENCODING"
fi
''')
        self.stub("setup", '''
printf "setup pwd=%s TMPDIR=%s\n" "$(pwd -P)" "$TMPDIR"
printf "setup HOME=%s CFFIXED_USER_HOME=%s\n" "$HOME" "$CFFIXED_USER_HOME"
''')

        for escape in ("yes", "no"):
            with self.subTest(escape=escape):
                attempt = self.run_wrapper(self.reviewed, self.root, PR_JSON=json.dumps(payload),
                                           TMPDIR=str(alias), ESCAPE=escape, OUTSIDE=str(outside),
                                           FIXTURE=str(self.bin_dir / "setup"),
                                           __CF_USER_TEXT_ENCODING="0x1F5:0:0")
                self.assertEqual(attempt.returncode, 2, attempt)
                log = Path(attempt.stdout.splitlines()[0].removeprefix("Validation log will be saved in "))
                if escape == "yes":
                    self.assertIn("outside the temporary root", attempt.stderr)
                    self.assertFalse(log.exists())
                else:
                    self.assertIn("No status was posted", attempt.stderr)
                    worktree = log.parent.resolve() / "worktree"
                    home = worktree.parent / "home"
                    build_tmp = worktree / "build" / ".tmp"
                    self.assertIn(f"setup pwd={worktree} TMPDIR={build_tmp}", log.read_text())
                    self.assertIn(f"setup HOME={home} CFFIXED_USER_HOME={home}", log.read_text())
                    self.assertIn("make build", log.read_text())
                    self.assertIn(f"test socket={build_tmp}/no-app.sock nix environment initialized=1",
                                  log.read_text())
                    self.assertIn(f"XCTest HOME={home} CFFIXED_USER_HOME={home} encoding=0x1F5:0:0",
                                  log.read_text())
                self.assertNotIn("/statuses/", self.log.read_text())

    def test_setup_names_its_build_directory_under_tmpdir(self):
        # Stop at the allocator so this checks the real setup script without building or cleaning anything.
        self.stub("mktemp", 'printf "mktemp args: %s\\n" "$*" >&2; exit 97')
        setup = WRAPPER.with_name("setup.sh")
        attempt = subprocess.run(["bash", str(setup), "--zmx-only"], text=True,
                                 capture_output=True, env=self.env, cwd=self.root)
        self.assertEqual(attempt.returncode, 97, attempt)
        self.assertIn(f"mktemp args: -d {self.root}/", attempt.stderr)


if __name__ == "__main__":
    unittest.main()
