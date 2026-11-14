"""Exercise the actual font fixture selector without importing GTK or starting the UI."""

import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "agterm-linux/tests/atspi_prefs_font.py"
tree = ast.parse(SOURCE.read_text())
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "specimen_window")
namespace = {"subprocess": subprocess}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
specimen_window = namespace["specimen_window"]


class FontWindowTests(unittest.TestCase):
    def select(self, windows="10485768\n10485764\n", active="10485764\n", owner="24936\n"):
        replies = {
            ("xdotool", "search", "--onlyvisible", "--pid", "24936"): windows,
            ("xdotool", "getactivewindow"): active,
            ("xdotool", "getwindowpid", active.strip()): owner,
        }
        def output(command, **kwargs):
            self.assertEqual(kwargs, {"text": True, "timeout": 10})
            return replies[tuple(command)]
        with patch.object(subprocess, "check_output", side_effect=output):
            return specimen_window(SimpleNamespace(pid=24936))

    def test_auxiliary_window_does_not_select_first_pid_match(self):
        self.assertEqual(self.select(), "10485764")

    def test_single_managed_window_remains_supported(self):
        self.assertEqual(self.select(windows="10485764\n"), "10485764")

    def test_foreign_active_window_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "belongs to PID"):
            self.select(owner="12345\n")

    def test_invisible_active_window_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "not visible"):
            self.select(windows="10485768\n")

    def test_window_manager_query_failure_is_not_hidden(self):
        with patch.object(subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "xdotool")):
            with self.assertRaises(subprocess.CalledProcessError):
                specimen_window(SimpleNamespace(pid=24936))


if __name__ == "__main__":
    unittest.main()
