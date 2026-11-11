"""Execute the hosted provenance blocks against a disposable checkout and payload."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


def workflow_python(name):
    step = WORKFLOW.read_text().split("      - name: " + name + "\n", 1)[1]
    step = step.split("      - name:", 1)[0]
    return textwrap.dedent(step.split("          python3 - <<'PYTHON'\n", 1)[1]
                          .split("          PYTHON", 1)[0])


class NativeSBOMWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "native-sbom"
        self.output.mkdir()
        self.lock = self.root / "agterm-linux/Package.resolved"
        self.lock.parent.mkdir()
        self.pinned = {"version": 3, "originHash": "original", "pins": [
            {"identity": name, "kind": "remoteSourceControl",
             "location": "https://example.invalid/" + name,
             "state": {"version": "1.2.3", "revision": revision * 40}}
            for name, revision in [("first", "a"), ("second", "b")]
        ]}
        self.lock.write_text(json.dumps(self.pinned))
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "RUNNER_TEMP": str(self.root), "BUILD_ARCH": "x86_64",
                    "GITHUB_REPOSITORY": "fixture/repo", "GITHUB_RUN_ID": "123",
                    "GITHUB_RUN_ATTEMPT": "2"}
        self.git("init")
        self.git("add", ".")
        self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
        self.env["SBOM_COMMIT"] = self.git("rev-parse", "HEAD").strip()
        self.verify = workflow_python("Bind source SBOM to this checkout and augment the staged payload")
        self.record = workflow_python("Record same-build SBOM and archive identities")

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], cwd=self.root, env=self.env,
                                       stderr=subprocess.DEVNULL, text=True)

    def run_block(self, code, **environment):
        return subprocess.run([sys.executable, "-c", code], cwd=self.root,
                              env={**self.env, **environment}, capture_output=True, text=True)

    def test_lock_accepts_format_changes_and_rejects_dependency_drift(self):
        for change in ("unchanged", "format", "version", "revision", "location", "kind"):
            with self.subTest(change=change):
                document = copy.deepcopy(self.pinned)
                if change == "format":
                    document.update(version=999, originHash="new-format")
                    document["pins"].reverse()
                elif change in ("version", "revision"):
                    document["pins"][0]["state"][change] = "drift"
                elif change in ("location", "kind"):
                    document["pins"][0][change] = "drift"
                self.lock.write_text(json.dumps(document, indent=4))
                retained = self.output / "linux.Package.resolved"
                retained.unlink(missing_ok=True)
                result = self.run_block(self.verify)
                accepted = change in ("unchanged", "format")
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                self.assertEqual(retained.exists(), accepted)
                if accepted:
                    self.assertEqual(retained.read_bytes(), self.lock.read_bytes())

    def test_provenance_hashes_documents_lock_and_archive_on_both_architectures(self):
        self.assertEqual(self.run_block(self.verify).returncode, 0)
        for architecture in ("x86_64", "aarch64"):
            with self.subTest(architecture=architecture):
                paths = [self.output / "sbom.spdx.json", self.output / "native.spdx.json",
                         self.output / "linux.Package.resolved",
                         self.root / f"agterm-omarchy-{architecture}.tar.gz"]
                for path in (paths[0], paths[1], paths[3]):
                    path.write_bytes(path.name.encode() * 100)
                result = self.run_block(self.record, BUILD_ARCH=architecture)
                self.assertEqual(result.returncode, 0, result.stderr)
                evidence = json.loads((self.output / "provenance.json").read_text())
                self.assertEqual(evidence, {
                    "commit": self.env["SBOM_COMMIT"], "architecture": architecture,
                    "repository": "fixture/repo", "run_id": "123", "run_attempt": "2",
                    "files": [{"name": path.name,
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                              for path in paths]})

    def test_provenance_rejects_checkout_mismatch_and_missing_archive(self):
        self.assertEqual(self.run_block(self.verify).returncode, 0)
        for name in ("sbom.spdx.json", "native.spdx.json"):
            (self.output / name).write_text("{}")
        result = self.run_block(self.record)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FileNotFoundError", result.stderr)
        self.assertFalse((self.output / "provenance.json").exists())
        (self.root / "agterm-omarchy-x86_64.tar.gz").write_bytes(b"payload")
        result = self.run_block(self.record, SBOM_COMMIT="0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source SBOM checkout mismatch", result.stderr)
        self.assertFalse((self.output / "provenance.json").exists())


if __name__ == "__main__":
    unittest.main()
