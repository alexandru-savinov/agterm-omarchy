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
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/omarchy-ci.yml").read_text()
        cls.gate = workflow.split("  ci:\n", 1)[1].split("\n  cookbook:\n", 1)[0]
        cls.script = textwrap.dedent(cls.gate.split("        run: |\n", 1)[1])
        cls.jobs = {"upstream-ci-disabled", "scripts", "changes", "test-linux", "lint-linux", "build-linux", "attest-linux",
                    "ghostty-patch-tests", "zmx-patch-tests", "cookbook"}

    def run_gate(self, overrides, *, attest=False):
        results = {job: {"result": overrides.get(job, "success")} for job in self.jobs}
        return subprocess.run(
            ["bash", "-eu", "-c", self.script],
            env={**os.environ, "RESULTS": json.dumps(results), "REQUIRE_ATTESTATION": str(attest).lower()},
            capture_output=True, text=True, timeout=5,
        )

    def test_gate_still_waits_for_every_required_job(self):
        needs = re.search(r"^    needs: \[(.+)\]$", self.gate, re.MULTILINE)
        self.assertIsNotNone(needs)
        self.assertEqual(set(needs.group(1).split(", ")), self.jobs)
        self.assertIn("    if: ${{ !cancelled() }}\n", self.gate)

    def test_success_and_legitimate_path_skips_pass(self):
        for skipped in ({}, {job: "skipped" for job in ("test-linux", "lint-linux", "build-linux", "ghostty-patch-tests",
                                                  "zmx-patch-tests", "cookbook")}):
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

    def test_upstream_workflow_guard_cannot_be_skipped(self):
        result = self.run_gate({"upstream-ci-disabled": "skipped"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upstream-ci-disabled", result.stderr)

    def test_only_a_main_build_requires_attestation_to_run(self):
        skipped = {"attest-linux": "skipped"}
        self.assertEqual(self.run_gate(skipped).returncode, 0)
        missing = self.run_gate(skipped, attest=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("attest-linux", missing.stderr)


if __name__ == "__main__":
    unittest.main()
