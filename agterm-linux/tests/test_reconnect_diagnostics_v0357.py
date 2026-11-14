#!/usr/bin/env python3
"""File/fake-only controls: python3 -B agterm-linux/tests/test_reconnect_diagnostics_v0357.py."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from atspi_reconnect_v0351 import (
    ReconnectDiagnostics, attachment_correlation, process_status, wrapper_events,
)

PANE = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
INCARNATION = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
NONCE = "a" * 64


class ReconnectMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="reconnect357-fake-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.log = self.root / (INCARNATION + ".log")
        self.write_log(self.line())
        self.write_stat(100, 50, "1234")
        self.write_stat(101, 100, "1235")
        self.before = process_status(101, self.proc)
        self.environment = [
            ("AGTERM_PANE_ID=" + PANE).encode(),
            ("AGTERM_DIAG357_INCARNATION=" + INCARNATION).encode(),
            ("AGTERM_DIAG357_NONCE_TAG=" + NONCE).encode(),
            b"PRIVATE_HOST=must-not-appear", b"SECRET=must-not-appear",
        ]

    def line(self, event="wrapper-start", status="na"):
        return (f"DIAG357 wrapper event={event} incarnation={INCARNATION} pane={PANE} "
                f"nonceTag={NONCE} pid=100 status={status}\n")

    def write_log(self, content):
        self.log.write_text(content)
        self.log.chmod(0o600)

    def write_stat(self, pid, parent, start):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        fields = ["0"] * 50
        fields[0], fields[1], fields[19] = "S", str(parent), start
        (directory / "stat").write_text(f"{pid} (fixture with ) parentheses) " + " ".join(fields))

    def correlate(self, before=None, environment=None, panes=None):
        return attachment_correlation(
            self.before if before is None else before,
            self.environment if environment is None else environment,
            {"left": PANE, "right": OTHER} if panes is None else panes,
            str(self.root), self.proc,
        )

    def test_correlates_selected_pane_to_matching_wrapper_and_start_identity(self):
        result = self.correlate()
        self.assertTrue(result["matched"])
        self.assertEqual(result["wrapper_before"]["pid"], 100)
        self.assertEqual(result["wrapper_before"]["start_ticks"], "1234")
        self.assertEqual(result["pane"], PANE)
        self.assertEqual(result["nonceTag"], NONCE)
        self.assertNotIn("must-not-appear", json.dumps(result))

    def test_refuses_mismatched_pane_nonce_parent_and_missing_tag(self):
        cases = [
            (self.before, self.environment, {"left": OTHER}),
            (dict(self.before, ppid=200), self.environment, {"left": PANE}),
            (dict(self.before, ppid=1), self.environment, {"left": PANE}),
            (self.before, [item for item in self.environment if not item.startswith(b"AGTERM_DIAG357_NONCE_TAG=")],
             {"left": PANE}),
            (self.before, [item.replace(NONCE.encode(), b"b" * 64) for item in self.environment], {"left": PANE}),
        ]
        for before, environment, panes in cases:
            with self.subTest(before=before, panes=panes):
                self.assertEqual(self.correlate(before, environment, panes), {"pid": 101, "matched": False})

    def test_refuses_ssh_reuse_or_parent_change_during_correlation(self):
        for parent, start in [(100, "9999"), (200, "1235")]:
            with self.subTest(parent=parent, start=start):
                self.write_stat(101, parent, start)
                self.assertFalse(self.correlate()["matched"])

    def test_process_reuse_returns_no_replacement_process_details(self):
        self.write_stat(100, 999, "new-start")
        self.assertEqual(process_status(100, self.proc, expected_start="1234"),
                         {"pid": 100, "identity_changed": True})
        self.assertEqual(process_status(999, self.proc), {"pid": 999, "unavailable": True})

    def test_metadata_reader_accepts_only_the_bounded_schema(self):
        malformed = [
            "private-host private-title private-environment\n",
            self.line().replace("status=na", "status=private-title"),
            self.line("ssh-return", "na"),
            self.line("unknown", "0"),
            self.line().replace("pane=" + PANE, "pane=private-title"),
            self.line().replace(INCARNATION, OTHER),
            self.line().rstrip() + " host=private-host\n",
            "x" * 65537,
            "private-\N{SNOWMAN}\n",
        ]
        for content in malformed:
            with self.subTest(content=content[:50]):
                self.write_log(content)
                result = wrapper_events(str(self.root), INCARNATION)
                self.assertEqual(result["events"], [])
                self.assertIn("error", result)
                self.assertNotIn("private", json.dumps(result))

    def test_metadata_reader_refuses_symlink_hardlink_and_fifo(self):
        target = self.root / "private-target"
        self.log.rename(target)
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.log.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, self.log)
                else:
                    os.mkfifo(self.log)
                self.assertEqual(wrapper_events(str(self.root), INCARNATION)["events"], [])
                self.log.unlink()
        self.assertEqual(target.read_text(), self.line())

    def test_metadata_reader_refuses_shared_file_or_directory(self):
        self.log.chmod(0o644)
        self.assertEqual(wrapper_events(str(self.root), INCARNATION)["events"], [])
        self.log.chmod(0o600)
        for mode in (0o755, 0o1700):
            self.root.chmod(mode)
            self.assertEqual(wrapper_events(str(self.root), INCARNATION)["events"], [])
        self.root.chmod(0o700)
        self.assertEqual(len(wrapper_events(str(self.root), INCARNATION)["events"]), 1)

    def test_disabled_observer_does_not_read_process_or_metadata_files(self):
        with patch.dict(os.environ, {}, clear=True):
            diagnostics = ReconnectDiagnostics()
        with patch("atspi_reconnect_v0351.attachment_correlation", side_effect=AssertionError("unexpected read")):
            diagnostics.correlate(self.before, self.environment, {"left": PANE})
        self.assertEqual(diagnostics.correlations, [])

    def test_report_keeps_the_recorded_start_identity_for_each_selected_process(self):
        with patch.dict(os.environ, {"AGTERM_DIAG357": "1", "AGTERM_DIAG357_DIR": str(self.root)}, clear=True):
            diagnostics = ReconnectDiagnostics()
        diagnostics.correlations = [self.correlate()]
        diagnostics.signalled = [self.before]
        with patch("atspi_reconnect_v0351.process_status", return_value={"unavailable": True}) as status:
            report = diagnostics.report("complete", str(self.root / "ssh-down"))
        status.assert_any_call(101, expected_start="1235")
        status.assert_any_call(100, expected_start="1234")
        self.assertTrue(report["correlations"][0]["matched"])
        self.assertEqual(len(report["correlations"][0]["wrapper_records"]["events"]), 1)
        self.assertNotIn("must-not-appear", json.dumps(report))

    def test_exact_shell_template_is_silent_preserves_stdin_and_saved_status(self):
        source = Path(__file__).resolve().parents[2] / "agtermCore/Sources/agtermCore/RemoteSession.swift"
        match = re.search(r'static let attachDiagnosticShell = #"""\n(.*?)\n    """#',
                          source.read_text(), re.DOTALL)
        self.assertIsNotNone(match)
        template = textwrap.dedent(match.group(1))
        subprocess.run(["/bin/sh", "-n"], input=template + "\n", text=True, check=True, timeout=5)
        program = template + """
        status=255
        diag357 ssh-return "$status"
        IFS= read -r payload
        printf '%s:%s\\n' "$payload" "$status"
        """
        base = {
            "PATH": "/usr/bin:/bin", "AGTERM_DIAG357": "1", "AGTERM_DIAG357_DIR": str(self.root),
            "AGTERM_DIAG357_INCARNATION": INCARNATION, "AGTERM_PANE_ID": PANE,
            "AGTERM_DIAG357_NONCE_TAG": NONCE,
        }
        variants = [
            ({}, True), ({"AGTERM_DIAG357": "0"}, False),
            ({"AGTERM_DIAG357_INCARNATION": "../private"}, False),
            ({"AGTERM_PANE_ID": "private-title\ninjected"}, False),
            ({"AGTERM_DIAG357_NONCE_TAG": "private-nonce"}, False),
        ]
        for extra, enabled in variants:
            with self.subTest(extra=extra):
                self.write_log("")
                result = subprocess.run(["/bin/sh", "-c", program], env=dict(base, **extra),
                                        input="fixture-input\n", text=True, capture_output=True,
                                        check=True, timeout=5)
                self.assertEqual(result.stdout, "fixture-input:255\n")
                self.assertEqual(result.stderr, "")
                records = wrapper_events(str(self.root), INCARNATION)["events"]
                self.assertEqual(len(records), int(enabled))
                if enabled:
                    self.assertEqual(records[0]["event"], "ssh-return")
                    self.assertEqual(records[0]["status"], "255")


if __name__ == "__main__":
    unittest.main(verbosity=2)
