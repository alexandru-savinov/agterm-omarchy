#!/usr/bin/env python3
"""default-renderer.py verdict: every scenario row present and PASS, evidence-only rows ignored."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("default_renderer", Path(__file__).resolve().parent / "default-renderer.py")
scenario = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scenario)


class VerdictTests(unittest.TestCase):
    def verdict(self, lines):
        with contextlib.redirect_stdout(io.StringIO()):
            return scenario.verdict("".join(f"{line}\n" for line in lines))

    def test_all_pass_with_evidence_rows(self):
        rows = ["mutation\tINFO none", "fresh_gl_renderer\tINFO llvmpipe"]
        self.assertEqual(self.verdict(rows + [f"{name}\tPASS x" for name in scenario.SCENARIO_ROWS]), 0)

    def test_one_fail(self):
        rows = [f"{name}\tPASS" for name in scenario.SCENARIO_ROWS]
        rows[3] = f"{scenario.SCENARIO_ROWS[3]}\tFAIL colours=1"
        self.assertEqual(self.verdict(rows), 1)

    def test_missing_row(self):
        self.assertEqual(self.verdict([f"{name}\tPASS" for name in scenario.SCENARIO_ROWS[:-1]]), 1)

    def test_any_failed_copy_fails(self):
        rows = [f"{name}\tPASS" for name in scenario.SCENARIO_ROWS] + [f"{scenario.FRESH_ROWS[0]}\tFAIL late"]
        self.assertEqual(self.verdict(rows), 1)

    def test_no_rows(self):
        self.assertEqual(self.verdict([]), 1)


if __name__ == "__main__":
    unittest.main()
