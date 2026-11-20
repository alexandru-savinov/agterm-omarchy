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


class ArchiveSigningTests(unittest.TestCase):
    def test_the_two_matrix_legs_sign_the_uploaded_archive_on_main_push(self):
        workflows = Path(__file__).resolve().parents[2] / ".github/workflows"
        ci = (workflows / "omarchy-ci.yml").read_text()
        build = ci.split("  build-linux:\n")[1].split("  ghostty-patch-tests:\n")[0]
        self.assertIn("- arch: x86_64", build)
        self.assertIn("- arch: aarch64", build)
        self.assertIn("id-token: write", build)
        self.assertIn("attestations: write", build)
        attest = build.split("- name: Attest the exact tested CI archive")[1].split("- name: Build summary")[0]
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", attest)
        self.assertRegex(attest, r"actions/attest@[0-9a-f]{40}")
        archive = "${{ runner.temp }}/agterm-omarchy-${{ matrix.arch }}.tar.gz"
        upload = build.split("- name: Upload tested runtime payload")[1].split("- name:", 1)[0]
        self.assertIn(f"path: {archive}", upload)
        self.assertIn(f"subject-path: {archive}", attest)
        native = (workflows / "omarchy-x86-native.yml").read_text()
        self.assertIn('.github/workflows/omarchy-ci.yml" --source-ref refs/heads/main', native)
        self.assertIn('--source-digest "$SHA" --deny-self-hosted-runners', native)


if __name__ == "__main__":
    unittest.main()
