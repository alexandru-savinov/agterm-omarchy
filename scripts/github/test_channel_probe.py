import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from channel_probe import verify


VALUE = "agterm-probe-" + "7f" * 32
DIGEST = hashlib.sha256(VALUE.encode()).hexdigest()


class ChannelProbeTests(unittest.TestCase):
    def test_matching_probe_proves_delivery(self):
        result = verify(VALUE, DIGEST, "github-issues")
        self.assertTrue(result["passed"])
        self.assertTrue(result["secret_delivery_verified"])
        self.assertEqual(result["channel"], "github-issues")
        self.assertNotIn(VALUE, json.dumps(result))

    def test_wrong_digest_fails_without_disclosing_value(self):
        with self.assertRaisesRegex(ValueError, "comparison failed"):
            verify(VALUE, "0" * 64, "github-issues")

    def test_missing_or_malformed_inputs_fail_closed(self):
        for value, digest, channel in (("", DIGEST, "github-issues"),
                                       ("not-a-disposable-probe", DIGEST, "github-issues"),
                                       (VALUE, "", "github-issues"),
                                       (VALUE, "x" * 64, "github-issues"),
                                       (VALUE, DIGEST, ""),
                                       (VALUE, DIGEST, "channel\n$(echo injection)")):
            with self.subTest(channel=channel, digest_valid=digest == DIGEST):
                with self.assertRaises(ValueError):
                    verify(value, digest, channel)

    def test_cli_artifact_and_summary_never_contain_the_probe(self):
        script = Path(__file__).with_name("channel_probe.py")
        for digest, status in ((DIGEST, 0), ("0" * 64, 1)):
            with self.subTest(expected_status=status), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "result.json"
                summary = Path(directory) / "summary.md"
                env = dict(os.environ, AGTERM_PROBE_VALUE=VALUE, AGTERM_PROBE_SHA256=digest,
                           AGTERM_PROBE_CHANNEL="github-issues", GITHUB_STEP_SUMMARY=str(summary))
                result = subprocess.run([sys.executable, str(script), "--output", str(output)],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, status)
                self.assertNotIn(VALUE, result.stdout + result.stderr + output.read_text() + summary.read_text())
                self.assertEqual(json.loads(output.read_text())["passed"], status == 0)
                self.assertIn("PASS:" if status == 0 else "FAIL:", summary.read_text())


if __name__ == "__main__":
    unittest.main()
