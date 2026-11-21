#!/usr/bin/env python3
"""compare-install.py accepts only the payload plus the installer's own files."""

import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("compare_install", Path(__file__).resolve().parent / "compare-install.py")
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


class CompareInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.stage = root / "agterm-stage"
        (self.stage / "bin").mkdir(parents=True)
        (self.stage / "share/agterm").mkdir(parents=True)
        (self.stage / "bin/agterm-linux.bin").write_bytes(b"\x7fELF app")
        (self.stage / "bin/agterm-linux.bin").chmod(0o755)
        (self.stage / "bin/agterm-linux").symlink_to("agterm-linux.bin")
        (self.stage / "share/agterm/COMMIT").write_text("a" * 40)
        self.installed = root / "lib/agterm"
        shutil.copytree(self.stage, self.installed, symlinks=True)
        for name in compare.INSTALLER_FILES:
            (self.installed / name).write_text("installer\n")

    def test_the_payload_plus_installer_files_matches(self):
        self.assertEqual(compare.compare(self.stage, self.installed), [])

    def test_any_other_difference_fails(self):
        cases = {
            "content": lambda: (self.installed / "bin/agterm-linux.bin").write_bytes(b"\x7fELF other"),
            "mode": lambda: (self.installed / "bin/agterm-linux.bin").chmod(0o644),
            "link target": lambda: ((self.installed / "bin/agterm-linux").unlink(),
                                    (self.installed / "bin/agterm-linux").symlink_to("/usr/bin/true")),
            "link replaced by file": lambda: ((self.installed / "bin/agterm-linux").unlink(),
                                              (self.installed / "bin/agterm-linux").write_text("#!/bin/sh\n")),
            "missing": lambda: (self.installed / "share/agterm/COMMIT").unlink(),
            "unexpected": lambda: (self.installed / "bin/extra").write_text("x"),
            "installer name as a link": lambda: ((self.installed / "share/agterm/RENDERER").unlink(),
                                                 (self.installed / "share/agterm/RENDERER").symlink_to("/etc/passwd")),
            "special file": lambda: os.mkfifo(self.installed / "bin/pipe"),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                change()
                self.assertNotEqual(compare.compare(self.stage, self.installed), [])


    def test_an_invalid_root_or_a_traversal_error_raises(self):
        missing = self.stage.parent / "missing"
        (self.stage.parent / "file").write_text("x")
        for stage, installed in ((missing, self.installed), (self.stage, missing),
                                 (self.stage.parent / "file", self.installed), (self.stage, self.installed / "bin/agterm-linux")):
            with self.subTest(stage=stage.name, installed=installed.name), self.assertRaises(OSError):
                compare.compare(stage, installed)
        for root in (self.stage, self.installed):
            (root / "share/agterm").chmod(0)
            self.addCleanup((root / "share/agterm").chmod, 0o755)
        with self.assertRaises(PermissionError):
            compare.compare(self.stage, self.installed)
        with patch.object(compare.os, "walk", side_effect=lambda *args, onerror, **kwargs: onerror(PermissionError("denied")) or iter(())), \
                self.assertRaises(PermissionError):
            compare.compare(self.stage, self.installed)


if __name__ == "__main__":
    unittest.main()
