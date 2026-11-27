#!/usr/bin/env python3
"""Offline checks for the MACOS workflow routing and pause step."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).parent / "fixtures/conditional-macos/sample-mismatch.json").read_text())
WORKFLOW = (ROOT / ".github/workflows/omarchy-macos.yml").read_text()
REF = "refs/heads/ci-state/conditional-macos"


class Workflow(unittest.TestCase):
    def test_decision_checks_out_base_sha_with_explicit_bootstrap(self):
        checkout = WORKFLOW.split("id: pilot-checkout", 1)[1].split("- name:", 1)[0]
        ref = next((line for line in checkout.splitlines() if "ref:" in line), "")
        self.assertIn("|| github.event.pull_request.base.sha", ref)
        self.assertIn("github.event.pull_request.head.ref == 'claude/conditional-ci-pilot'", ref)

    def test_main_pushes_have_independent_groups_and_are_not_cancelled(self):
        concurrency = WORKFLOW.split("concurrency:\n", 1)[1].split("\njobs:", 1)[0]
        self.assertIn("github.event_name == 'push' && format('push-{0}', github.run_id)", concurrency)
        self.assertIn("cancel-in-progress: ${{ github.event_name != 'pull_request' && github.event_name != 'push' }}",
                      concurrency)

    @unittest.skipUnless(shutil.which("jq"), "pause step requires jq")
    def test_sample_pause_outcomes(self):
        job = WORKFLOW.split("\n  sample-mismatch:\n", 1)[1]
        script = dedent(job.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
        annotation = FIXTURE["annotation"]
        cases = {
            "create": (0, "Confirmed " + REF, True),
            "repeat": (0, "Confirmed existing " + REF, True),
            "race": (0, "Confirmed " + REF, True),
            "deny": (1, "Conditional macOS pause failed", False),
            "missing": (1, "Conditional macOS pause failed", False),
        }
        with tempfile.TemporaryDirectory() as scratch:
            flag = Path(scratch) / "flag.json"
            gh = Path(scratch) / "gh"
            gh.write_text(f"""#!{sys.executable}
import os
import sys
from pathlib import Path

args = sys.argv[1:]
flag = Path(__file__).with_name("flag.json")
case = os.environ["PAUSE_CASE"]
if any("matching-refs" in arg for arg in args):
    print("true" if flag.exists() else "false")
elif any(arg.endswith("/blobs") for arg in args):
    flag.with_suffix(".pending").write_text(next(arg[8:] for arg in args if arg.startswith("content=")))
    print("b" * 40)
elif any(arg.endswith("/trees") for arg in args):
    sys.stdin.read()
    print("c" * 40)
elif any(arg.endswith("/commits") for arg in args):
    sys.stdin.read()
    print("d" * 40)
elif any(arg.endswith("/refs") for arg in args):
    assert "ref={REF}" in args and "sha=" + "d" * 40 in args
    if case in ("create", "race"):
        flag.with_suffix(".pending").replace(flag)
    if case in ("race", "deny"):
        raise SystemExit(1)
else:
    raise AssertionError(args)
""")
            gh.chmod(0o755)
            for case, (code, message, paused) in cases.items():
                with self.subTest(case=case):
                    if case != "repeat":
                        flag.unlink(missing_ok=True)
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", script], text=True, capture_output=True,
                        env={**os.environ, "PATH": f"{scratch}:{os.environ['PATH']}", "PAUSE_CASE": case,
                             "GITHUB_RUN_ID": annotation["run"], "GITHUB_RUN_ATTEMPT": annotation["attempt"],
                             "GITHUB_SHA": annotation["merge"]},
                    )
                    self.assertIn(message, result.stdout, result)
                    self.assertEqual(result.returncode, code, result)
                    line = next(line for line in result.stdout.splitlines()
                                if line.startswith("::error title=Conditional macOS sample mismatch::"))
                    self.assertEqual(json.loads(line.split("::", 2)[2]), annotation)
                    self.assertEqual(flag.exists(), paused)
                    if paused:
                        self.assertEqual(json.loads(flag.read_text()), annotation)
        self.assertIn("run: exit 1", job, "confirmed mismatch must fail the workflow for Errorflow")


if __name__ == "__main__":
    unittest.main()
