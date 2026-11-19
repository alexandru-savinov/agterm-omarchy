#!/usr/bin/env python3
"""stage-evidence.py copies only regular files and directories, and fails on anything else."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("stage_evidence", Path(__file__).resolve().parent / "stage-evidence.py")
staging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(staging)


class StageEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "out"
        (self.source / "probe/run-1").mkdir(parents=True)
        (self.source / "gates.tsv").write_text("ordinary\tPASS 7/7\n")
        (self.source / "probe/run-1/report.json").write_text("{}")
        self.secret = self.root / "runner-secret"
        self.secret.write_text("token")

    def test_copies_regular_files_and_directories(self):
        self.assertEqual(staging.stage(self.source, self.root / "staged"), [])
        self.assertEqual((self.root / "staged/probe/run-1/report.json").read_text(), "{}")
        self.assertEqual((self.root / "staged/gates.tsv").read_text(), "ordinary\tPASS 7/7\n")

    def test_never_follows_or_copies_links_or_special_files(self):
        (self.source / "gates-link.tsv").symlink_to(self.secret)
        (self.source / "probe/escape").symlink_to(self.root)
        os.mkfifo(self.source / "pipe")
        staged = self.root / "staged"
        self.assertEqual(sorted(staging.stage(self.source, staged)), ["gates-link.tsv", "pipe", "probe/escape"])
        self.assertEqual((staged / "rejected.txt").read_text(), "gates-link.tsv\npipe\nprobe/escape\n")
        copied = [path for path in staged.rglob("*")]
        self.assertFalse(any(path.is_symlink() for path in copied))
        self.assertFalse(any(path.read_bytes() == b"token" for path in copied if path.is_file()))

    def test_refuses_an_existing_target(self):
        (self.root / "staged").mkdir()
        with self.assertRaises(FileExistsError):
            staging.stage(self.source, self.root / "staged")


    def test_a_scan_error_or_an_invalid_source_raises(self):
        blocked = self.source / "probe"
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o755)
        with self.assertRaises(PermissionError):
            staging.stage(self.source, self.root / "staged")
        with self.assertRaises(OSError):
            staging.stage(self.root / "missing", self.root / "staged-missing")
        with self.assertRaises(NotADirectoryError):
            staging.stage(self.secret, self.root / "staged-file")


if __name__ == "__main__":
    unittest.main()
