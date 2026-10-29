#!/usr/bin/env python3
"""Exercise the embedded smoke runner's actual finally block after a failed restart."""
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "test-omarchy.sh"
PYTHON = SOURCE.read_text().split("exec python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
MODULE = ast.parse(PYTHON)
CLEANUP = next(node for node in MODULE.body
               if isinstance(node, ast.FunctionDef) and node.name == "cleanup_live_daemons")
FINALLY = next(node.finalbody for node in reversed(MODULE.body) if isinstance(node, ast.Try))
CODE = compile(ast.fix_missing_locations(ast.Module(body=[CLEANUP, *FINALLY], type_ignores=[])),
               str(SOURCE), "exec")
OWNED = "agterm-" + "1" * 32
FOREIGN = "agterm-" + "2" * 32


class FailedRestartCleanupTests(unittest.TestCase):
    def scenario(self, *, missing_process=False, fail_kill=False, live=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, artifacts = root / "run", root / "artifacts"
            state = run / "state"
            state.mkdir(parents=True)
            artifacts.mkdir()
            (state / "settings.json").write_text('{}')
            (artifacts / "result.json").write_text('{"passed": true}')
            calls = []
            remaining = {OWNED, FOREIGN}

            def invoke(argv, **kwargs):
                self.assertTrue(run.exists(), "state removed before daemon cleanup")
                calls.append(argv)
                self.assertEqual(kwargs["env"]["ZMX_DIR"], "/tmp/agterm-zmx-0123456789abcdef")
                self.assertNotIn("ZMX_SESSION", kwargs["env"])
                self.assertNotIn("ZMX_SESSION_PREFIX", kwargs["env"])
                if argv[1] == "kill":
                    self.assertEqual(argv, ["/test/zmx", "kill", OWNED, "--force"])
                    if fail_kill:
                        raise subprocess.CalledProcessError(1, argv)
                    remaining.remove(OWNED)
                    return SimpleNamespace(stdout="", returncode=0)
                self.assertEqual(argv[1:], ["list"])
                return SimpleNamespace(stdout="".join(f"name={name}\tclients=0\n" for name in remaining),
                                       returncode=0)

            namespace = dict(subprocess=subprocess, json=json, re=re, shutil=shutil,
                             app=None if missing_process else SimpleNamespace(poll=lambda: 1),
                             run=run, state=state, artifacts=artifacts, checks=[],
                             env={"ZMX_SESSION": "inherited", "ZMX_SESSION_PREFIX": "unsafe"},
                             live_endpoint={"executable": "/test/zmx",
                                            "socketDirectory": "/tmp/agterm-zmx-0123456789abcdef"},
                             live_daemons={OWNED} if live else set())
            with patch("subprocess.run", side_effect=invoke):
                if fail_kill:
                    with self.assertRaisesRegex(AssertionError, "state retained"):
                        exec(CODE, namespace)
                    self.assertTrue(run.exists())
                    self.assertFalse(json.loads((artifacts / "result.json").read_text())["passed"])
                else:
                    exec(CODE, namespace)
                    self.assertFalse(run.exists())
                    self.assertEqual(remaining, {FOREIGN} if live else {OWNED, FOREIGN})
            self.assertTrue((artifacts / "state/settings.json").exists())
            return calls

    def test_exited_replacement_cleans_only_saved_test_daemons(self):
        self.assertEqual(len(self.scenario()), 3)

    def test_missing_replacement_process_still_cleans_saved_daemons(self):
        self.assertEqual(len(self.scenario(missing_process=True)), 3)

    def test_failed_backend_cleanup_preserves_recovery_state_and_fails_result(self):
        self.scenario(fail_kill=True)

    def test_fresh_shell_mode_does_not_invoke_zmx(self):
        self.assertEqual(self.scenario(live=False), [])


if __name__ == "__main__":
    unittest.main()
