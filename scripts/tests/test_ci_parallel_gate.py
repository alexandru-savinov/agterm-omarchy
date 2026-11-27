"""Independent GTK scheduling must not let failures bypass the aggregate gate."""

import json
import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[2]


class ParallelGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (REPO / ".github/workflows/omarchy-ci.yml").read_text()
        cls.gate = cls.workflow.split("  ci:\n", 1)[1].split("\n  cookbook:\n", 1)[0]
        cls.script = textwrap.dedent(cls.gate.split("        run: |\n", 1)[1])
        cls.jobs = {"upstream-ci-disabled", "scripts", "changes", "test-linux", "lint-linux", "build-linux", "attest-linux",
                    "ghostty-patch-tests", "zmx-patch-tests", "cookbook", "claude-hook-replay"}

    def run_gate(self, overrides, *, attest=False):
        results = {job: {"result": overrides.get(job, "success")} for job in self.jobs}
        return subprocess.run(
            ["bash", "-eu", "-c", self.script],
            env={**os.environ, "RESULTS": json.dumps(results), "REQUIRE_ATTESTATION": str(attest).lower(),
                 "REQUIRE_CLAUDE_REPLAY": str(results["build-linux"]["result"] == "success").lower()},
            capture_output=True, text=True, timeout=5,
        )

    def test_gate_still_waits_for_every_required_job(self):
        needs = re.search(r"^    needs: \[(.+)\]$", self.gate, re.MULTILINE)
        self.assertIsNotNone(needs)
        self.assertEqual(set(needs.group(1).split(", ")), self.jobs)
        self.assertIn("    if: ${{ !cancelled() }}\n", self.gate)

    def test_success_and_legitimate_path_skips_pass(self):
        for skipped in ({}, {job: "skipped" for job in ("test-linux", "lint-linux", "build-linux", "ghostty-patch-tests",
                                                  "zmx-patch-tests", "cookbook", "claude-hook-replay")}):
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

    def test_successful_build_requires_replay_to_run(self):
        result = self.run_gate({"claude-hook-replay": "skipped"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("claude-hook-replay", result.stderr)

    def test_claude_replay_requires_a_valid_workflow_upstream_tag(self):
        workflow_env = self.workflow.split("\nenv:\n", 1)[1].split("\n\n", 1)[0]
        self.assertRegex(workflow_env, r'(?m)^  UPSTREAM_BASE_TAG: "v[0-9]+\.[0-9]+\.[0-9]+"$')
        job = self.workflow.split("\n  claude-hook-replay:\n", 1)[1].split("\n  attest-linux:\n", 1)[0]
        script = textwrap.dedent(job.split("        run: |\n", 1)[1])
        base_env = {"PATH": os.defpath, "RUNNER_TEMP": "unused", "GITHUB_SHA": "unused"}
        for tag, message in (({}, "requires a workflow-level upstream version"),
                             ({"UPSTREAM_BASE_TAG": "v"}, "Invalid Claude replay package version: .0")):
            with self.subTest(tag=tag):
                result = subprocess.run(["bash", "-eu", "-c", script], cwd=REPO, env=base_env | tag,
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
