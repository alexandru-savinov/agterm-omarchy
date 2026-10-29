#!/usr/bin/env python3
"""Adversarial provenance/architecture checks without network or executing payloads."""

import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("promotion", Path(__file__).resolve().parents[1] / "prepare-omarchy-prerelease.py")
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)
SHA = "a" * 40


def payload(arch="aarch64", sha=SHA, extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        files = {"share/agterm/COMMIT": sha.encode(), "share/agterm/VERSION": b"dev"}
        header = bytearray(b"\x7fELF\x02\x01" + b"\0" * 14)
        header[18:20] = promotion.ARCHITECTURES[arch].to_bytes(2, "little")
        files.update({"bin/" + name: bytes(header) for name in ("agterm-linux.bin", "agtermctl.bin", "zmx")})
        for name, data in files.items():
            member = tarfile.TarInfo("agterm-stage/" + name)
            member.size = len(data)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    return output.getvalue()


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.run = {"repository": {"full_name": promotion.REPOSITORY},
                    "head_repository": {"full_name": promotion.REPOSITORY},
                    "path": promotion.WORKFLOW, "workflow_id": 42, "name": "Omarchy CI", "status": "completed",
                    "conclusion": "success", "event": "workflow_dispatch", "head_branch": "omarchy", "head_sha": SHA}
        self.workflow = {"id": 42, "name": "Omarchy CI"}

    def test_trusted_run(self):
        promotion.validate_run(self.run, self.workflow, SHA, SHA)

    def test_rejects_untrusted_or_incomplete_runs(self):
        for key, value in (("repository", {"full_name": "other/repo"}),
                           ("head_repository", {"full_name": "other/repo"}),
                           ("path", "other.yml"), ("workflow_id", 99), ("name", "build"), ("event", "pull_request"),
                           ("head_branch", "unreviewed"), ("status", "in_progress"),
                           ("conclusion", "failure"), ("head_sha", "b" * 40)):
            with self.subTest(key=key):
                run = dict(self.run, **{key: value})
                with self.assertRaises(ValueError):
                    promotion.validate_run(run, self.workflow, SHA, SHA)

    def test_rejects_moved_head_and_abbreviated_commit(self):
        for sha, branch in ((SHA, "b" * 40), (SHA[:12], SHA)):
            with self.assertRaises(ValueError):
                promotion.validate_run(self.run, self.workflow, sha, branch)

    def test_both_architectures(self):
        for arch in promotion.ARCHITECTURES:
            self.assertEqual(promotion.validate_payload(payload(arch), arch, SHA), "dev")

    def test_rejects_wrong_source_and_architecture(self):
        with self.assertRaises(ValueError):
            promotion.validate_payload(payload(sha="b" * 40), "aarch64", SHA)
        with self.assertRaises(ValueError):
            promotion.validate_payload(payload("x86_64"), "aarch64", SHA)

    def test_rejects_unsafe_paths_and_links(self):
        for name in ("/absolute", "agterm-stage/../../escape", "elsewhere/file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=tarfile.TarInfo(name)), "aarch64", SHA)
        for target in ("/etc/passwd", "../../escape"):
            member = tarfile.TarInfo("agterm-stage/bin/link")
            member.type = tarfile.SYMTYPE
            member.linkname = target
            with self.subTest(target=target), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    def test_allows_internal_library_links(self):
        member = tarfile.TarInfo("agterm-stage/lib/libswiftCore.so")
        member.type = tarfile.SYMTYPE
        member.linkname = "libswiftCore.so.1"
        promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    def test_rejects_duplicate_members_and_devices(self):
        duplicate = tarfile.TarInfo("agterm-stage/share/agterm/COMMIT")
        device = tarfile.TarInfo("agterm-stage/device")
        device.type = tarfile.CHRTYPE
        for member in (duplicate, device):
            with self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    def test_ci_gates_cannot_be_skipped(self):
        names = ["Installer and scripts", "SwiftLint (Linux)", "CI"]
        for arch in promotion.ARCHITECTURES:
            names += [f"Core tests ({arch})", f"GTK build and tests ({arch})"]
        steps = [{"name": name, "conclusion": "success"} for name in (
            "swift test (agterm-linux)", "swift build Release/WMO (agterm-linux)",
            "Validate staged Linux payload", "GTK AT-SPI smoke tests", "Upload tested runtime payload")]
        jobs = [{"name": name, "conclusion": "success", "steps": copy.deepcopy(steps)} for name in names]
        promotion.validate_jobs(jobs)
        for index in range(len(jobs)):
            failed = copy.deepcopy(jobs)
            failed[index]["conclusion"] = "skipped"
            with self.subTest(job=failed[index]["name"]), self.assertRaises(ValueError):
                promotion.validate_jobs(failed)
        jobs[-1]["steps"][3]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            promotion.validate_jobs(jobs)

    def test_artifact_digest_and_zip_identity(self):
        data = io.BytesIO()
        original = payload()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("agterm-omarchy-aarch64.tar.gz", original)
        zipped = data.getvalue()
        artifact = {"id": 123, "expired": False, "workflow_run": {"head_sha": SHA},
                    "digest": "sha256:" + hashlib.sha256(zipped).hexdigest()}
        with patch.object(promotion.subprocess, "check_output", return_value=zipped):
            name, actual = promotion.download_artifact(artifact, "aarch64", SHA)
            self.assertEqual(name, "agterm-omarchy-aarch64.tar.gz")
            self.assertEqual(actual, original)
            for key, value in (("expired", True), ("digest", "sha256:" + "0" * 64),
                               ("workflow_run", {"head_sha": "b" * 40})):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    promotion.download_artifact(dict(artifact, **{key: value}), "aarch64", SHA)
            with self.assertRaises(ValueError):
                promotion.download_artifact(artifact, "x86_64", SHA)


if __name__ == "__main__":
    unittest.main()
