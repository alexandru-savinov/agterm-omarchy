#!/usr/bin/env python3
"""inspect-uninstall.py: an inspection that fails to run is a FAIL, never zero leftovers."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("inspect_uninstall", Path(__file__).resolve().parent / "inspect-uninstall.py")
inspect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect)


def pgrep(code, stdout="", stderr=""):
    return patch.object(inspect.subprocess, "run", return_value=subprocess.CompletedProcess([], code, stdout, stderr))


class InspectUninstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefix = self.root / "agt-prefix"
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()

    def row(self, status=0):
        argv = ["inspect-uninstall.py", "--prefix", str(self.prefix), "--user", "omarchy",
                "--uninstall-status", str(status), "--evidence", str(self.evidence)]
        output = io.StringIO()
        with patch("sys.argv", argv), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_:
            inspect.main()
        return output.getvalue().strip(), exit_.exception.code

    def test_an_absent_or_empty_prefix_and_no_process_pass(self):
        for make in (lambda: None, self.prefix.mkdir):
            with self.subTest(make=make), pgrep(1):
                make()
                self.assertEqual(self.row(), ("PASS exit=0 files_left=0 stray_processes=0", 0))

    def test_leftovers_strays_and_a_failed_uninstaller_fail(self):
        (self.prefix / "share").mkdir(parents=True)
        with pgrep(1):
            self.assertEqual(self.row(), ("FAIL exit=0 files_left=1 stray_processes=0", 1))
        self.prefix.joinpath("share").rmdir()
        with pgrep(0, "42 /home/omarchy/agt-prefix/lib/agterm/bin/zmx\n7 bash /repo/.github/omarchy-x86-native/gates.sh\n"):
            self.assertEqual(self.row(), ("FAIL exit=0 files_left=0 stray_processes=1", 1))
        with pgrep(1):
            self.assertEqual(self.row(status=3), ("FAIL exit=3 files_left=0 stray_processes=0", 1))

    def test_inspection_errors_fail(self):
        self.prefix.mkdir()
        with pgrep(2, stderr="pgrep: invalid user name"):
            row, code = self.row()
        self.assertEqual(code, 1)
        self.assertTrue(row.startswith("FAIL exit=0 inspection error: pgrep exited 2"), row)
        blocked = self.prefix / "blocked"
        (blocked / "inner").mkdir(parents=True)
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o755)
        with pgrep(1):
            row, code = self.row()
        self.assertEqual(code, 1)
        self.assertTrue(row.startswith("FAIL exit=0 inspection error:"), row)


if __name__ == "__main__":
    unittest.main()
