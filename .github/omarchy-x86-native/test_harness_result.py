import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("harness-result.py")
RETRY_LINE = b"agterm: GTK EGL compatibility retry matched 15 configs (desktop OpenGL)\n"


class HarnessResultTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        (self.root / "cleanup.json").write_text(json.dumps({"passed": True, "tracked_daemon_count": 0}))

    def result(self, gtk_configs, *, status=0, name="ordinary"):
        expected = 9 if name == "live" else 7
        (self.root / "result.json").write_text(json.dumps(
            {"passed": True, "checks": list(range(expected)), "identity": {"harness": {"matches_commit": True}}}))
        return subprocess.check_output(
            [sys.executable, "-I", str(SCRIPT), str(self.root), str(expected), str(status), name, gtk_configs], text=True)

    def test_zero_configs_requires_retry_in_each_app_log(self):
        log = self.root / "app.log"
        for name, expected in (("ordinary", 7), ("live", 9)):
            with self.subTest(name=name):
                log.unlink(missing_ok=True)
                for message in (None, b"GTK chose no EGL configuration\n"):
                    if message is not None:
                        log.write_bytes(message)
                    row = self.result("0", name=name)
                    self.assertIn(f"{name}\tFAIL {expected}/{expected}", row)
                    self.assertIn("egl_retry=missing_from_app.log", row)
                self.assertIn("egl_retry=missing_from_app.log", self.result("00", name=name))

                log.write_bytes(RETRY_LINE)
                row = self.result("0", name=name)
                self.assertIn(f"{name}\tPASS {expected}/{expected}", row)
                self.assertIn("egl_retry=logged", row)
                self.assertIn(f"{name}\tFAIL {expected}/{expected}", self.result("0", status=1, name=name))

    def test_invalid_config_counts_fail_even_with_retry_evidence(self):
        (self.root / "app.log").write_bytes(RETRY_LINE)
        for name in ("ordinary", "live"):
            for gtk_configs in ("", " 0", "unknown"):
                with self.subTest(name=name, gtk_configs=gtk_configs):
                    row = self.result(gtk_configs, name=name)
                    self.assertIn(f"{name}\tFAIL", row)
                    self.assertIn("egl_retry=unknown_gtk_configs", row)

    def test_positive_configs_do_not_require_retry(self):
        for name, expected in (("ordinary", 7), ("live", 9)):
            with self.subTest(name=name):
                row = self.result("15", name=name)
                self.assertIn(f"{name}\tPASS {expected}/{expected}", row)
                self.assertNotIn("egl_retry=", row)


if __name__ == "__main__":
    unittest.main()
