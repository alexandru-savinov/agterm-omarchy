#!/usr/bin/env python3
"""Adversarial provenance/architecture checks without network or executing payloads."""

import copy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
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
        for member in (extra if isinstance(extra, list) else [extra] if extra else []):
            archive.addfile(member)
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

    @staticmethod
    def link(name, target, kind=tarfile.SYMTYPE):
        member = tarfile.TarInfo("agterm-stage/" + name)
        member.type = kind
        member.linkname = target
        return member

    def test_accepts_link_chains_that_stay_inside_the_stage(self):
        for links in ([("lib/libfoo.so", "libfoo.so.1"), ("lib/libfoo.so.1", "libfoo.so.1.2")],
                      [("share/lib", "../lib"), ("share/core", "lib/libswiftCore.so"),
                       ("lib/libswiftCore.so", "../lib/./libswiftCore.so.1")],
                      [("share/agterm/up", ".."), ("share/peer", "agterm/up/agterm/VERSION")]):
            with self.subTest(links=links):
                members = [self.link(name, target) for name, target in links]
                promotion.validate_payload(payload(extra=members), "aarch64", SHA)
        hard = self.link("bin/zmx-copy", "agterm-stage/bin/zmx", tarfile.LNKTYPE)
        promotion.validate_payload(payload(extra=hard), "aarch64", SHA)

    def test_rejects_link_chains_that_resolve_outside_the_stage(self):
        # Each link is lexically inside agterm-stage; only the resolved chain leaves it.
        for links in ([("a/b/up", ".."), ("a/b/escape", "up/../../x")],
                      [("lib/self", "."), ("lib/deep/loop", "../self/../../x")],
                      [("a/up", "../share/agterm/.."), ("a/escape", "up/../../x")],
                      [("x", "y"), ("y", "x")]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                members = [self.link(name, target) for name, target in links]
                promotion.validate_payload(payload(extra=members), "aarch64", SHA)

    def test_resolves_links_reached_through_directory_links(self):
        # Each target is lexically inside agterm-stage; only resolving the directory links shows the result.
        accepted = [("share/libs", "../lib"), ("bin/core", "../share/libs/libswiftCore.so")]
        promotion.validate_payload(payload(extra=[self.link(*item) for item in accepted]), "aarch64", SHA)
        for links in ([("share/libs", "../lib"), ("bin/tool", "../share/libs/../../x")],
                      [("lib/sub", "../share"), ("q", "lib/sub/.."), ("p", "q/../x")],
                      [("share/libs", "../lib"), ("share/libs-up", "libs/.."), ("bin/tool", "../share/libs-up/../x")]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=[self.link(*item) for item in links]), "aarch64", SHA)

    def ci_archive(self, links=()):
        # Archive a real directory tree exactly as ci.yml does: tar -czf OUT -C PARENT agterm-stage.
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "agterm-stage"
            header = bytearray(b"\x7fELF\x02\x01" + b"\0" * 14)
            header[18:20] = promotion.ARCHITECTURES["aarch64"].to_bytes(2, "little")
            for name, data in {"share/agterm/COMMIT": SHA.encode() + b"\n", "share/agterm/VERSION": b"dev\n",
                               "lib/libswiftCore.so.1": b"library", "bin/agterm-linux.bin": bytes(header),
                               "bin/agtermctl.bin": bytes(header), "bin/zmx": bytes(header)}.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755)
            (stage / "lib/libswiftCore.so").symlink_to("libswiftCore.so.1")
            (stage / "share/icons/hicolor").mkdir(parents=True)
            for name, target in links:
                (stage / name).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, stage / name)
            output = Path(temp) / "payload.tar.gz"
            subprocess.run([shutil.which("tar"), "-czf", str(output), "-C", temp, "agterm-stage"], check=True)
            return output.read_bytes()

    @unittest.skipUnless(shutil.which("tar"), "tar unavailable")
    def test_accepts_payload_archived_like_ci_with_directory_entries(self):
        data = self.ci_archive()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            first = archive.getmembers()[0]
            self.assertTrue(first.isdir() and first.name == "agterm-stage", "fixture must carry the root entry")
        self.assertEqual(promotion.validate_payload(data, "aarch64", SHA), "dev")

    @unittest.skipUnless(shutil.which("tar"), "tar unavailable")
    def test_rejects_escaping_chain_in_payload_archived_like_ci(self):
        data = self.ci_archive([("a/b/up", ".."), ("a/b/escape", "up/../../x")])
        with self.assertRaises(ValueError):
            promotion.validate_payload(data, "aarch64", SHA)

    def test_rejects_non_canonical_member_spellings(self):
        # A link map keyed on one spelling must not miss a directory link written with another.
        for links in ([("./lib/x/s", ".."), ("lib/x/s/l", "../../../x")],
                      [("/lib/x/s", ".."), ("lib/x/s/l", "../../../x")]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=[self.link(*item) for item in links]), "aarch64", SHA)
        below = tarfile.TarInfo("agterm-stage/lib/x/s/payload")
        with self.assertRaises(ValueError):
            promotion.validate_payload(payload(extra=[self.link("/lib/x/s", ".."), below]), "aarch64", SHA)

    def test_rejects_members_and_hardlinks_through_links(self):
        stage_link = self.link("share/out", "../lib")
        below = tarfile.TarInfo("agterm-stage/share/out/planted")
        through = self.link("bin/copy", "agterm-stage/share/out/../../x", tarfile.LNKTYPE)
        for members in ([stage_link, below], [stage_link, through]):
            with self.subTest(members=[member.name for member in members]), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=members), "aarch64", SHA)

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
