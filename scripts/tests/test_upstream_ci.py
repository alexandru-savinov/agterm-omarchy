import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("upstream_ci", Path(__file__).resolve().parents[1] / "check-upstream-ci.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class WorkflowGuardTests(unittest.TestCase):
    def setUp(self):
        self.linux_port = {guard.CI: b"100644 blob linux-port", guard.AUR: b"aur",
                           guard.LEGACY_RELEASE: b"upstream legacy"}
        self.upstream = {guard.CI: b"100644 blob umputun"}
        self.local = {guard.CI: self.linux_port[guard.CI], guard.LEGACY_RELEASE: b"downstream legacy"}

    def errors(self, state="disabled_manually"):
        workflows = [{"path": guard.CI, "state": state}] if state else []
        return guard.check_layout(self.linux_port, self.upstream, self.local, [{"workflows": workflows}])

    def test_accepts_disabled_exact_linux_port_ci(self):
        self.assertEqual(self.errors(), [])

    def test_rejects_active_and_missing_registry_entries(self):
        for state in ("active", "disabled_inactivity", None):
            with self.subTest(state=state):
                self.assertIn("must be disabled manually", self.errors(state)[0])

    def test_rejects_edited_ci_and_umputun_ci(self):
        for entry in (b"100644 blob edited", self.upstream[guard.CI]):
            with self.subTest(entry=entry):
                self.local[guard.CI] = entry
                self.assertIn("must be byte-identical", self.errors()[0])

    def test_rejects_a_new_workflow_from_either_source(self):
        for source in (self.linux_port, self.upstream):
            with self.subTest(source=source):
                source[".github/workflows/new.yaml"] = b"100644 blob new"
                self.assertIn("new upstream workflow requires an explicit disposition", self.errors()[0])
                del source[".github/workflows/new.yaml"]

    def test_rejects_restoring_upstream_aur_publishing(self):
        self.local[guard.AUR] = self.linux_port[guard.AUR]
        self.assertIn("must remain absent", self.errors()[0])


if __name__ == "__main__":
    unittest.main()
