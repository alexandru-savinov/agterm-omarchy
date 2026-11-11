"""Independent GTK scheduling must not let failures bypass the aggregate gate."""

import json
import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest


class ParallelGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text()
        cls.gate = workflow.split("  ci:\n", 1)[1].split("\n  cookbook:\n", 1)[0]
        cls.script = textwrap.dedent(cls.gate.split("        run: |\n", 1)[1])
        cls.jobs = {"scripts", "changes", "test-linux", "lint-linux", "build-linux", "cookbook"}

    def run_gate(self, overrides):
        results = {job: {"result": overrides.get(job, "success")} for job in self.jobs}
        return subprocess.run(
            ["bash", "-eu", "-c", self.script],
            env={**os.environ, "RESULTS": json.dumps(results)},
            capture_output=True, text=True, timeout=5,
        )

    def test_gate_still_waits_for_every_required_job(self):
        needs = re.search(r"^    needs: \[(.+)\]$", self.gate, re.MULTILINE)
        self.assertIsNotNone(needs)
        self.assertEqual(set(needs.group(1).split(", ")), self.jobs)
        self.assertIn("    if: always()\n", self.gate)

    def test_success_and_legitimate_path_skips_pass(self):
        for skipped in ({}, {job: "skipped" for job in ("test-linux", "lint-linux", "build-linux", "cookbook")}):
            with self.subTest(skipped=skipped):
                result = self.run_gate(skipped)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_each_failure_or_cancellation_rejects_other_successes(self):
        for job in self.jobs:
            for status in ("failure", "cancelled"):
                with self.subTest(job=job, status=status):
                    result = self.run_gate({job: status})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(job, result.stderr)


if __name__ == "__main__":
    unittest.main()
