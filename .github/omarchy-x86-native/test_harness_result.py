import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("harness-result.py")


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

    def test_zero_configs_requires_retry_in_the_app_log(self):
        log = self.root / "app.log"
        for message in (None, b"GTK chose no EGL configuration\n"):
            with self.subTest(message=message):
                if message is not None:
                    log.write_bytes(message)
                self.assertIn("ordinary\tFAIL 7/7", self.result("0"))
                self.assertIn("egl_retry=missing_from_app.log", self.result("0"))

        log.write_bytes(b"agterm: GTK EGL compatibility retry matched 15 configs (desktop OpenGL)\n")
        self.assertIn("ordinary\tPASS 7/7", self.result("0"))
        self.assertIn("egl_retry=logged", self.result("0"))
        self.assertIn("ordinary\tFAIL 7/7", self.result("0", status=1))

    def test_positive_configs_and_live_do_not_need_the_ordinary_retry(self):
        self.assertIn("ordinary\tPASS 7/7", self.result("15"))
        self.assertIn("live\tPASS 9/9", self.result("0", name="live"))


if __name__ == "__main__":
    unittest.main()
