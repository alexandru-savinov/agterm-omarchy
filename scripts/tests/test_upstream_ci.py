import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("upstream_ci", Path(__file__).resolve().parents[1] / "check-upstream-ci.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class WorkflowGuardTests(unittest.TestCase):
    def setUp(self):
        self.linux_port = {path: f"100644 blob {path}".encode() for path in guard.INHERITED}
        self.umputun_ci = ".github/workflows/ci.yml"
        self.upstream = {self.umputun_ci: b"100644 blob umputun"}
        self.local = self.linux_port.copy()

    def errors(self, changed_path=None, state=None):
        workflows = [{"path": path, "state": state if path == changed_path else "disabled_manually"}
                     for path in guard.INHERITED if path != changed_path or state is not None]
        return guard.check_layout(self.linux_port, self.upstream, self.local, [{"workflows": workflows}])

    def test_accepts_all_three_exact_disabled_linux_port_workflows(self):
        self.assertEqual(guard.INHERITED, {self.umputun_ci, ".github/workflows/release-linux.yml",
                                           ".github/workflows/publish-aur.yml"})
        self.assertEqual(self.errors(), [])

    def test_rejects_active_and_missing_registry_entries_for_every_path(self):
        for path in guard.INHERITED:
            for state in ("active", "disabled_inactivity", None):
                with self.subTest(path=path, state=state):
                    self.assertIn(f"{path} must be disabled manually", self.errors(path, state)[0])

    def test_rejects_each_edited_workflow_and_umputuns_ci(self):
        for path in guard.INHERITED:
            with self.subTest(path=path):
                self.local[path] = b"100644 blob edited"
                self.assertIn(f"{path} must be byte-identical", self.errors()[0])
                self.local[path] = self.linux_port[path]
        self.local[self.umputun_ci] = self.upstream[self.umputun_ci]
        self.assertIn(f"{self.umputun_ci} must be byte-identical", self.errors()[0])

    def test_rejects_a_new_workflow_from_either_source(self):
        for source in (self.linux_port, self.upstream):
            with self.subTest(source=source):
                source[".github/workflows/new.yaml"] = b"100644 blob new"
                self.assertIn("new upstream workflow requires an explicit disposition", self.errors()[0])
                del source[".github/workflows/new.yaml"]


class ArchiveSigningTests(unittest.TestCase):
    def test_read_only_build_and_separate_signer_use_the_same_artifact(self):
        workflows = Path(__file__).resolve().parents[2] / ".github/workflows"
        ci = (workflows / "omarchy-ci.yml").read_text()
        build = ci.split("  build-linux:\n")[1].split("  attest-linux:\n")[0]
        signer = ci.split("  attest-linux:\n")[1].split("  ghostty-patch-tests:\n")[0]
        for arch in ("x86_64", "aarch64"):
            self.assertIn(f"- arch: {arch}", build)
        self.assertIn("arch: [x86_64, aarch64]", signer)
        self.assertIn("needs: build-linux", signer)
        self.assertIn("needs.build-linux.result == 'success'", signer)
        self.assertIn("github.ref == 'refs/heads/main'", signer)
        self.assertIn("github.event_name != 'pull_request'", signer)
        self.assertNotIn("github.event_name == 'push'", signer)
        for grant in ("id-token: write", "attestations: write"):
            self.assertNotIn(grant, build)
            self.assertIn(grant, signer)
        self.assertNotIn("actions/checkout", signer)
        self.assertRegex(signer, r"actions/attest@[0-9a-f]{40}")
        archive = "agterm-omarchy-${{ matrix.arch }}.tar.gz"
        artifact = "name: agterm-omarchy-${{ matrix.arch }}-${{ github.sha }}"
        runner_temp = "${{ runner.temp }}"
        upload = build.split("- name: Upload tested runtime payload")[1].split("- name:", 1)[0]
        self.assertIn(artifact, upload)
        self.assertIn(artifact, signer)
        self.assertIn(f"path: {runner_temp}/{archive}", upload)
        self.assertIn(f"subject-path: {runner_temp}/sign/{archive}", signer)
        self.assertIn("digest-mismatch: error", signer)
        native = (workflows / "omarchy-x86-native.yml").read_text()
        self.assertIn('.github/workflows/omarchy-ci.yml" --source-ref refs/heads/main', native)
        self.assertIn('--source-digest "$SHA" --deny-self-hosted-runners', native)


if __name__ == "__main__":
    unittest.main()
