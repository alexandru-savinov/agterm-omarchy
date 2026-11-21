#!/usr/bin/env python3
"""ci-artifact.py against the promotion tests' fake GitHub API; no network, no payload execution."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = load("ci_artifact", HERE / "ci-artifact.py")
fixture = load("promotion_fixture", HERE.parents[1] / "scripts/tests/test_omarchy_prerelease.py")


def github(**changes):
    fake = fixture.FakeGitHub(**changes)
    fake.add_release_gates()
    return fake


class CIArtifactTests(unittest.TestCase):
    @staticmethod
    def identity(fake, arch):
        artifact = next(item for item in fake.artifacts if item["name"].startswith(f"agterm-omarchy-{arch}-"))
        return ["--run-id", "7", "--attempt", "1", "--arch", arch, "--artifact-id", str(artifact["id"]),
                "--transport-digest", artifact["digest"]]

    def call(self, fake, *argv):
        first, second = fake.patches()
        stdout = io.StringIO()
        with first, second, patch("sys.argv", ["ci-artifact.py", *argv]), contextlib.redirect_stdout(stdout):
            ci.main()
        return stdout.getvalue()

    def test_download_writes_the_verified_archive_and_checksums(self):
        fake = github()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "release"
            self.call(fake, "download", *self.identity(fake, "aarch64"), "--output", str(output))
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["SHA256SUMS", "agterm-omarchy-aarch64.tar.gz", "source.json"])
            for line in (output / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ")
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            with zipfile.ZipFile(io.BytesIO(fake.zips["aarch64"])) as archive:
                self.assertEqual((output / "agterm-omarchy-aarch64.tar.gz").read_bytes(),
                                 archive.read("agterm-omarchy-aarch64.tar.gz"))

    def test_empty_run_id_resolves_the_newest_releasable_main_run(self):
        fake = github()
        fake.add_run(9, "2026-11-02T00:00:00Z")
        resolved = json.loads(self.call(fake, "resolve", "--run-id", ""))
        self.assertEqual((resolved["run_id"], resolved["attempt"], resolved["sha"]), (9, 1, fixture.SHA))
        for arch in ci.promotion.ARCHITECTURES:
            artifact = next(item for item in fake.runs[9][2] if item["name"] == f"agterm-omarchy-{arch}-{fixture.SHA}")
            self.assertEqual(resolved["artifacts"][arch], {"id": artifact["id"], "name": artifact["name"],
                                                          "transport_digest": artifact["digest"]})

    def test_rejects_runs_a_versioned_release_would_not_take(self):
        cases = {
            "omarchy branch": ({"branch": "omarchy"}, {}, "only main"),
            "pull request": ({}, {"event": "pull_request"}, "PR merge runs"),
            "red run": ({}, {"conclusion": "failure"}, "not green"),
        }
        for label, (made, changed, message) in cases.items():
            with self.subTest(label):
                fake = github(**made)
                fake.run.update(changed)
                with self.assertRaisesRegex(ValueError, message):
                    self.call(fake, "resolve", "--run-id", "7")
        fake = github()
        fake.jobs = [job for job in fake.jobs if job["name"] != "GTK build and tests (x86_64)"]
        fake.runs[7] = (fake.run, fake.jobs, fake.artifacts)
        with self.assertRaisesRegex(ValueError, "GTK build and tests"):
            self.call(fake, "resolve", "--run-id", "7")
        with patch.object(ci.promotion, "on_main", return_value=False), self.assertRaisesRegex(ValueError, "not a commit on main"):
            self.call(github(), "resolve", "--run-id", "7")

    def test_download_rejects_a_changed_attempt_artifact_or_transfer(self):
        fake = github()
        changed = {"attempt": ["--attempt", "2"], "artifact": ["--artifact-id", "999"],
                   "transport digest": ["--transport-digest", "sha256:" + "0" * 64]}
        for label, override in changed.items():
            argv = self.identity(fake, "x86_64")
            argv[argv.index(override[0]) + 1] = override[1]
            message = "attempt changed" if label == "attempt" else "Artifact changed"
            with self.subTest(label), tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, message):
                self.call(fake, "download", *argv, "--output", str(Path(temp) / "out"))
        identity = self.identity(fake, "x86_64")
        fake.zips["x86_64"] = fixture.zipped("x86_64", "b" * 40)
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.call(fake, "download", *identity, "--output", str(Path(temp) / "out"))


if __name__ == "__main__":
    unittest.main()
