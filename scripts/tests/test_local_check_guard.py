#!/usr/bin/env python3
"""Test the wrapper's guard and status fragments without running its entry point."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "local-check.sh"


class LocalCheckGuard(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.scratch = self.root / "agterm-local-check.fixture"
        self.worktree = self.scratch / "worktree"
        self.worktree.mkdir(parents=True)
        (self.scratch / "git").mkdir()
        self.outside = self.root.with_name(self.root.name + "-outside")
        self.outside.mkdir()
        self.addCleanup(self.outside.rmdir)
        self.wrapper = WRAPPER.read_text()

    def shell(self, fragment, **variables):
        return subprocess.run(
            ["/bin/bash", "-euo", "pipefail", "-c", fragment],
            cwd=self.root, capture_output=True, text=True,
            env={"PATH": os.environ["PATH"], "temp_root": str(self.root),
                 "scratch": str(self.scratch), "worktree": str(self.worktree),
                 "log": str(self.scratch / "validation.log"),
                 "versions": str(self.scratch / "versions.json"), **variables},
        )

    def post_checkout(self, before_exit="", *, isolated_setup=True):
        scripts = self.worktree / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "setup.sh").write_text(
            "# Supports AGTERM_LOCAL_CHECK_ROOT from scripts/local-check.sh.\n"
            if isolated_setup else "# older setup\n"
        )
        fragment = self.wrapper.split('repo_git worktree add --quiet --detach "$worktree" "$commit"\n', 1)[1]
        fragment = fragment.split("run_isolated() {", 1)[0]
        helpers = self.wrapper.split("# BEGIN local path guard\n", 1)
        if len(helpers) == 2:
            fragment = helpers[1].split("# END local path guard\n", 1)[0] + "\n" + fragment
        return self.shell(
            'isolated_git() { [[ "$*" == *"rev-parse"* ]] && echo fixture; return 0; }\n'
            'repo_git() { printf "cleanup %s\\n" "$*"; }\n'
            + fragment + '\nprintf "ready\\n"\n' + before_exit
        )

    def test_rejects_escaping_setup_and_cleanup_paths(self):
        for relative in (
            "build/.local-check-home", "build/.local-check-home/Library/Caches/org.swift.swiftpm",
            "build", "build/.tmp", "build/DerivedData", "agtermCore/.build",
            "agterm/Resources", "agterm/Resources/ghostty/themes", "GhosttyKit.xcframework",
        ):
            with self.subTest(path=relative):
                with tempfile.TemporaryDirectory(dir=self.root) as checkout:
                    self.worktree = Path(checkout) / "worktree"
                    self.scratch = self.worktree.parent
                    self.worktree.mkdir()
                    (self.scratch / "git").mkdir()
                    target = self.worktree / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(self.outside, target_is_directory=True)
                    result = self.post_checkout()
                    self.assertEqual(result.returncode, 2, result)
                    self.assertIn(str(target), result.stderr)
                    self.assertNotIn("ready", result.stdout)
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(list(self.outside.iterdir()), [])

    def test_refuses_cleanup_if_checkout_was_replaced(self):
        result = self.post_checkout(
            'mv "$worktree" "$scratch/original"\n'
            f'ln -s "{self.outside}" "$worktree"\n'
        )
        self.assertIn("ready", result.stdout)
        self.assertEqual(result.returncode, 2, result)
        self.assertIn(str(self.worktree), result.stderr)
        self.assertNotIn("cleanup worktree remove", result.stdout)

    def test_valid_checkout_reaches_ready_and_cleanup(self):
        result = self.post_checkout()
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("ready", result.stdout)
        self.assertIn("cleanup worktree remove", result.stdout)
        self.assertTrue((self.worktree / "build/.local-check-home").is_dir())
        self.assertTrue((self.worktree / "build/.tmp").is_dir())

    def test_refuses_setup_without_isolation_support(self):
        result = self.post_checkout(isolated_setup=False)
        self.assertEqual(result.returncode, 2, result)
        self.assertIn("lacks isolated setup support", result.stderr)
        self.assertNotIn("ready", result.stdout)

    def test_setup_refuses_external_zig_header_mutation(self):
        include = self.outside / "include"
        include.mkdir()
        header = include / "float.h"
        header.write_text("#ifndef __CLANG_FLOAT_H\n#define __CLANG_FLOAT_H\n#endif\n")
        self.addCleanup(include.rmdir)
        self.addCleanup(header.unlink)
        setup = WRAPPER.with_name("setup.sh").read_text()
        fragment = setup.split("SHIM_MARK=", 1)[1].split("# Metal Toolchain", 1)[0]
        result = self.shell(
            r'zig() { echo "lib_dir = \"$ZIG_LIB\""; }' "\n"
            'mktemp() { echo "unexpected mktemp" >&2; return 97; }\n'
            'ZMX_ZIG=zig\nlocal_check_root=$worktree\nSHIM_MARK=' + fragment + "\npatch_zig_float_h",
            ZIG_LIB=str(self.outside),
        )
        self.assertEqual(result.returncode, 2, result)
        self.assertIn(str(header), result.stderr)
        self.assertNotIn("unexpected mktemp", result.stderr)

    def test_known_product_failure_withholds_success(self):
        fragment = self.wrapper.split("# Status descriptions", 1)[1]
        fragment = "# Status descriptions" + fragment.split('exit "$exit_code"', 1)[0]
        versions = self.scratch / "versions.json"
        versions.write_text("{}")
        log = self.scratch / "validation.log"
        for observed, expected in (("0", "1"), ("2", "2")):
            with self.subTest(observed=observed):
                log.write_text("fixture\n")
                result = self.shell(
                    'gh() { cat; }\n' + fragment,
                    url="https://example.invalid", commit="a" * 40, tree="b" * 40,
                    repo="fixture/repo", versions=str(versions), exit_code=observed,
                )
                self.assertEqual(result.returncode, 0, result)
                status = json.loads(result.stdout)
                self.assertEqual(status["state"], "failure")
                self.assertIn(f" exit={expected} ", status["description"])


if __name__ == "__main__":
    unittest.main()
