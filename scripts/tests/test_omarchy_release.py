#!/usr/bin/env python3
"""Versioned release assets: VERSION stamp, root ownership, RUNPATH, naming and the omarchy-pkgs watch."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("release", REPO / "scripts/omarchy-release.py")
elf_fixture = load("elf_fixture", TESTS / "test_linux_runpath.py").elf
SHA = "c" * 40
# Verbatim from omarchy-pkgs' upstream-watch configuration quoted in #462.
OMARCHY_PKGS_WATCH = r"omarchy-v(?P<version>[0-9]+(?:\.[0-9]+)*)"


def elf(arch, search="$ORIGIN/../lib"):
    data = bytearray(elf_fixture(search)[0])
    data[18:20] = release.promotion.ARCHITECTURES[arch].to_bytes(2, "little")
    return bytes(data)


SHIPPED_THEME = b"background = 1a1b26\n"
INVENTORY = json.dumps({"themes": {"files": [
    {"file": "TokyoNight", "sha256": hashlib.sha256(SHIPPED_THEME).hexdigest(), "attribution": "resolved"},
    {"file": "Dracula", "sha256": "0" * 64, "attribution": "dropped", "reason": "No license file"}]}}).encode()


def archive(arch="x86_64", sha=SHA, version=b"dev\n", uid=1001, override=None):
    files = {"share/agterm/COMMIT": sha.encode() + b"\n", "share/agterm/VERSION": version,
             "bin/agterm-linux": b"#!/bin/sh\n", "bin/agtermctl": b"#!/bin/sh\n",
             "bin/agterm-linux.bin": elf(arch), "bin/agtermctl.bin": elf(arch), "bin/zmx": elf(arch, "$ORIGIN"),
             "lib/libswiftCore.so": elf(arch, "$ORIGIN"),
             "share/licenses/agterm/LICENSE": (REPO / "LICENSE").read_bytes(),
             "share/licenses/agterm/payload.json": b'{"unresolved_libraries": []}\n',
             "share/licenses/agterm/inventory.json": INVENTORY, "share/ghostty/themes/TokyoNight": SHIPPED_THEME,
             "share/ghostty/themes/.agterm-resource-manifest": b"manifest\n"}
    files.update(override or {})
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.GNU_FORMAT) as target:
        root = tarfile.TarInfo("agterm-stage")
        root.type, root.mode, root.uid, root.gid, root.mtime = tarfile.DIRTYPE, 0o755, uid, uid, 1794991057
        target.addfile(root)
        for name, data in files.items():
            if data is None:
                continue
            member = tarfile.TarInfo("agterm-stage/" + name)
            member.size, member.mode, member.uid, member.gid, member.mtime = len(data), 0o755, uid, uid, 1794991057
            member.uname = member.gname = "builder"
            target.addfile(member, io.BytesIO(data))
    return output.getvalue()


def read(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
        return {member.name: (member, source.extractfile(member).read() if member.isfile() else None)
                for member in source.getmembers()}


class ReleaseAssetTests(unittest.TestCase):
    def test_stamp_changes_only_version_and_owns_everything_as_root(self):
        for arch in release.promotion.ARCHITECTURES:
            with self.subTest(arch=arch):
                source = archive(arch)
                stamped = release.stamp(source, "0.41.1.1")
                before, after = read(source), read(stamped)
                self.assertEqual(list(before), list(after))
                for name, (member, data) in after.items():
                    self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""), name)
                    expected = b"0.41.1.1\n" if name == release.VERSION_MEMBER else before[name][1]
                    self.assertEqual(data, expected, name)
                evidence = release.validate_release(source, stamped, arch, SHA, "0.41.1.1")
                self.assertEqual(evidence["changed_members"], ["agterm-stage/share/agterm/VERSION"])
                self.assertEqual(evidence["asset"], f"agterm-omarchy-0.41.1.1-{arch}.tar.gz")

    def test_stamp_is_deterministic(self):
        self.assertEqual(release.stamp(archive(), "0.41.1.2"), release.stamp(archive(), "0.41.1.2"))
        self.assertNotEqual(release.stamp(archive(), "0.41.1.2"), release.stamp(archive(), "0.41.1.3"))

    def test_rejects_release_not_owned_by_root(self):
        source = archive()
        unowned = archive(version=b"0.41.1.1\n")
        with self.assertRaisesRegex(ValueError, "root:root"):
            release.validate_release(source, unowned, "x86_64", SHA, "0.41.1.1")

    def test_rejects_wrong_version_or_any_other_content_change(self):
        source = archive()
        with self.assertRaisesRegex(ValueError, "VERSION"):
            release.validate_release(source, release.stamp(source, "0.41.1.2"), "x86_64", SHA, "0.41.1.1")
        changed = release.stamp(archive(override={"bin/agterm-linux": b"#!/bin/sh\nexit 1\n"}), "0.41.1.1")
        with self.assertRaisesRegex(ValueError, "changed content|differs"):
            release.validate_release(source, changed, "x86_64", SHA, "0.41.1.1")

    def test_rejects_absolute_or_missing_runpath(self):
        absolute = "/usr/lib/swift/linux:$ORIGIN:/__w/agterm-omarchy/agterm-omarchy/agterm-linux/vendor/ghostty/lib"
        for name, data, message in (("bin/agterm-linux.bin", elf("x86_64", absolute), "RUNPATH is not"),
                                    ("bin/agtermctl.bin", elf("x86_64", "$ORIGIN"), "RUNPATH is not"),
                                    ("lib/libswiftCore.so", elf("x86_64", "/usr/lib/swift/linux"), "absolute search path")):
            with self.subTest(name=name):
                source = archive(override={name: data})
                with self.assertRaisesRegex(ValueError, message):
                    release.validate_release(source, release.stamp(source, "0.41.1.1"), "x86_64", SHA, "0.41.1.1")

    def test_rejects_missing_license_or_unreviewed_library(self):
        for override, message in (({"share/licenses/agterm/LICENSE": None}, "LICENSE"),
                                  ({"share/licenses/agterm/payload.json": b'{"unresolved_libraries": ["libx.so"]}'},
                                   "without reviewed notices")):
            with self.subTest(message=message):
                source = archive(override=override)
                with self.assertRaisesRegex(ValueError, message):
                    release.validate_release(source, release.stamp(source, "0.41.1.1"), "x86_64", SHA, "0.41.1.1")

    def test_rejects_themes_without_a_resolved_author_license(self):
        for override, message in (({"share/ghostty/themes/Dracula": b"background = 282a36\n"}, "without a resolved"),
                                  ({"share/ghostty/themes/TokyoNight": b"background = 000000\n"}, "differ from their"),
                                  ({"share/licenses/agterm/inventory.json": None}, "no licensed theme inventory")):
            with self.subTest(message=message):
                source = archive(override=override)
                with self.assertRaisesRegex(ValueError, message):
                    release.validate_release(source, release.stamp(source, "0.41.1.1"), "x86_64", SHA, "0.41.1.1")

    def test_rejects_source_that_is_not_a_tested_ci_archive(self):
        for source in (archive(sha="d" * 40), archive(version=b"0.41.1.1\n")):
            with self.assertRaises(ValueError):
                release.validate_release(source, release.stamp(source, "0.41.1.1"), "x86_64", SHA, "0.41.1.1")

    def test_asset_names(self):
        self.assertEqual(release.asset_name("0.41.1.1", "x86_64"), "agterm-omarchy-0.41.1.1-x86_64.tar.gz")
        self.assertEqual(release.asset_name("0.41.1.12", "aarch64"), "agterm-omarchy-0.41.1.12-aarch64.tar.gz")
        with self.assertRaises(ValueError):
            release.asset_name("0.41.1.1", "arm64")


class ReleaseTagTests(unittest.TestCase):
    def test_watch_pattern_is_the_omarchy_pkgs_one(self):
        self.assertEqual(release.WATCH_TAG.pattern, OMARCHY_PKGS_WATCH)

    def test_release_tags_match_the_whole_watch_pattern(self):
        for serial in (1, 2, 9, 10, 123):
            version = f"0.41.1.{serial}"
            with self.subTest(version=version):
                tag = release.release_tag(version, "0.41.1")
                self.assertEqual(tag, f"omarchy-v{version}")
                self.assertEqual(re.fullmatch(OMARCHY_PKGS_WATCH, tag)["version"], version)

    def test_rejects_versions_the_watch_or_vercmp_would_misread(self):
        for version in ("0.41.1", "0.41.1.0", "0.41.1.01", "0.41.2.1", "0.41.1.1.1", "0.41.1.1rc1",
                        "0.41.1.1-1", "v0.41.1.1", "0.41.1.1 ", "", "0.41.1.x"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.release_tag(version, "0.41.1")

    def test_development_prerelease_tags_never_match_the_watch(self):
        tag = release.promotion.promotion_tag("a" * 40, 43761095738)
        self.assertIsNone(re.fullmatch(OMARCHY_PKGS_WATCH, tag))

    def test_rehearsal_uses_serial_zero_only(self):
        self.assertEqual(release.release_tag("0.41.1.0", "0.41.1", rehearsal=True), "omarchy-v0.41.1.0")
        with self.assertRaises(ValueError):
            release.release_tag("0.41.1.1", "0.41.1", rehearsal=True)

    def test_upstream_base_comes_from_ci(self):
        tag = re.search(r'^  UPSTREAM_BASE_TAG: "v(.+)"$', (REPO / ".github/workflows/omarchy-ci.yml").read_text(), re.MULTILINE)
        self.assertEqual(release.upstream_base(), tag[1])


class CommandAndWorkflowTests(unittest.TestCase):
    def test_rehearsal_command_writes_asset_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "agterm-omarchy-aarch64.tar.gz"
            source.write_bytes(archive("aarch64"))
            result = subprocess.run([sys.executable, "-I", str(REPO / "scripts/omarchy-release.py"), str(source),
                                     "--arch", "aarch64", "--sha", SHA, "--output", str(Path(temp) / "out")],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            version = release.upstream_base() + ".0"
            asset = Path(temp) / "out" / f"agterm-omarchy-{version}-aarch64.tar.gz"
            evidence = json.loads(Path(f"{asset}.json").read_text())
            self.assertEqual(evidence["release_archive_sha256"], release.digest(asset.read_bytes()))
            self.assertEqual(read(asset.read_bytes())[release.VERSION_MEMBER][1], f"{version}\n".encode())
            again = subprocess.run([sys.executable, "-I", str(REPO / "scripts/omarchy-release.py"), str(source),
                                    "--arch", "aarch64", "--sha", SHA, "--output", str(Path(temp) / "out")],
                                   capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already exists", again.stderr)

    def test_ci_archives_as_root_and_rehearses_on_both_arches(self):
        workflow = (REPO / ".github/workflows/omarchy-ci.yml").read_text()
        self.assertIn('tar --owner=0 --group=0 --numeric-owner -czf "$RUNNER_TEMP/agterm-omarchy-${BUILD_ARCH}.tar.gz"',
                      workflow)
        upload = workflow.index("- name: Upload tested runtime payload")
        rehearsal = workflow.index("- name: Rehearse versioned release archive")
        self.assertLess(upload, rehearsal)
        self.assertIn('scripts/verify-omarchy-release-archive.sh "$rehearsal/$asset" "$version"', workflow)
        self.assertIn("name: release-rehearsal-${{ matrix.arch }}-${{ github.sha }}", workflow)


if __name__ == "__main__":
    unittest.main()
