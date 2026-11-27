#!/usr/bin/env python3
"""Exercise wrapper refusals without running the macOS gates or writing a status."""
import os
import json
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
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.log = self.root / "commands"
        self.env = {**os.environ, "HOME": str(self.root), "PATH": f"{bin_dir}:{os.environ['PATH']}",
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
            path = bin_dir / name
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


if __name__ == "__main__":
    unittest.main()
