#!/usr/bin/env python3
"""Run/harness/binary identity in the smoke runner's records, and the zmx listing positive control.

The helpers and the real finally block are extracted from scripts/test-omarchy.sh, as in
test_omarchy_cleanup.py. Set AGTERM_IDENTITY_TEST_ZMX to a real zmx executable to also exercise the
positive control against it (positive and wrong-directory cases); it is skipped otherwise.
"""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / "test-omarchy.sh"
PYTHON = SOURCE.read_text().split("exec python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
MODULE = ast.parse(PYTHON)
HELPERS = [node for node in MODULE.body if isinstance(node, ast.FunctionDef) or (
    isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["SENTINEL_PREFIX"])]
FINALLY = next(node.finalbody for node in reversed(MODULE.body) if isinstance(node, ast.Try))
HELPER_CODE = compile(ast.fix_missing_locations(ast.Module(body=HELPERS, type_ignores=[])), str(SOURCE), "exec")
FINALLY_CODE = compile(ast.fix_missing_locations(ast.Module(body=FINALLY, type_ignores=[])), str(SOURCE), "exec")
OWNED = "agterm-" + "1" * 32
ENDPOINT = "/tmp/agterm-zmx-0123456789abcdef"
SHA256 = re.compile(r"[0-9a-f]{64}")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")


def namespace(**extra):
    ns = dict(subprocess=subprocess, json=json, re=re, shutil=shutil, os=os, time=time, uuid=uuid,
              hashlib=hashlib, datetime=datetime, timezone=timezone, Path=Path, env={})
    exec(HELPER_CODE, ns)
    ns.update(extra)
    return ns


class FakeZmx:
    """Pinned-CLI-shaped zmx double; each knob models one way an empty listing can lie."""

    def __init__(self, test, *, list_exit=0, blind=False, lose_sentinel=False, sentinel_unconfirmed=False):
        self.test, self.list_exit, self.blind = test, list_exit, blind
        self.lose_sentinel, self.sentinel_unconfirmed = lose_sentinel, sentinel_unconfirmed
        self.sessions, self.calls, self.sentinel = {OWNED}, [], None

    def __call__(self, argv, **kwargs):
        self.calls.append(argv[1:])
        self.test.assertEqual(kwargs["env"]["ZMX_DIR"], ENDPOINT)
        self.test.assertFalse(kwargs["check"])
        verb = argv[1]
        if verb == "run":
            self.sentinel = argv[2]
            self.sessions.add(argv[2])
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if verb == "kill":
            name = argv[2]
            self.sessions.discard(name)
            unconfirmed = name == self.sentinel and self.sentinel_unconfirmed
            return SimpleNamespace(stdout="", returncode=0,
                                   stderr="" if unconfirmed else f"killed session {name}\n")
        self.test.assertEqual(verb, "list")
        if self.list_exit:
            return SimpleNamespace(stdout="", stderr="error: socket directory unusable\n", returncode=self.list_exit)
        if self.blind:  # wrong/missing ZMX_DIR: exit 0, empty stdout, notice on stderr
            return SimpleNamespace(stdout="", stderr="no sessions found in /elsewhere\n", returncode=0)
        return SimpleNamespace(stdout="".join(f"  name={n}\tclients=0\n" for n in sorted(self.sessions)),
                               stderr="", returncode=0)

    def teardown(self):
        """App teardown closes the test daemon; a lost sentinel models a vanished socket directory."""
        self.sessions.discard(OWNED)
        if self.lose_sentinel:
            self.sessions.discard(self.sentinel)


class FinallyRecordTests(unittest.TestCase):
    """Executes the actual finally block and reads back cleanup.json / result.json."""

    def run_finally(self, zmx):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, artifacts = root / "run", root / "artifacts"
            state = run / "state"
            state.mkdir(parents=True)
            artifacts.mkdir()
            (state / "settings.json").write_text("{}")
            (artifacts / "result.json").write_text('{"passed": true}')
            identity = {"schema": "agterm-omarchy-run-identity/1", "run_id": "r" * 32,
                        "started_at_utc": "2026-10-30T00:00:00.000000Z", "zmx_list": [], "positive_control": None}
            app = SimpleNamespace(poll=lambda: None, terminate=zmx.teardown, wait=lambda timeout: 0, kill=lambda: None)
            ns = namespace(app=app, run=run, state=state, artifacts=artifacts, checks=["a"], identity=identity,
                           live_endpoint={"executable": "/test/zmx", "socketDirectory": ENDPOINT},
                           live_daemons={OWNED}, sessions=lambda: [], ctl=lambda *a: {})
            failed = False
            with patch("subprocess.run", side_effect=zmx):
                try:
                    exec(FINALLY_CODE, ns)
                except AssertionError as error:
                    self.assertIn("state retained", str(error))
                    failed = True
            cleanup = json.loads((artifacts / "cleanup.json").read_text())
            result = json.loads((artifacts / "result.json").read_text())
            self.assertEqual(cleanup["passed"], not failed)
            return cleanup, result

    def assert_identity(self, record):
        ident = record["identity"]
        self.assertEqual(ident["run_id"], "r" * 32)
        self.assertRegex(ident["started_at_utc"], STAMP)
        self.assertRegex(ident["ended_at_utc"], STAMP)
        for entry in ident["zmx_list"]:
            self.assertEqual(set(entry), {"at_utc", "exit_code", "stdout_rows", "stderr_bytes"})
        return ident

    def test_passing_cleanup_records_identity_inventory_and_positive_control(self):
        zmx = FakeZmx(self)
        cleanup, result = self.run_finally(zmx)
        self.assertTrue(cleanup["passed"])
        self.assertEqual(cleanup["tracked_daemon_count"], 1)
        ident = self.assert_identity(cleanup)
        control = ident["positive_control"]
        self.assertEqual(control, {"name": zmx.sentinel, "created": True, "listed_before_teardown": True,
                                   "listed_after_teardown": True, "killed_confirmed": True,
                                   "absent_after_kill": True, "passed": True})
        self.assertTrue(ident["zmx_list"])
        self.assertTrue(all(e["exit_code"] == 0 and e["stderr_bytes"] == 0 for e in ident["zmx_list"]))
        self.assertNotIn(zmx.sentinel, zmx.sessions, "sentinel must not outlive the run")
        # Sentinel is created before the app teardown and killed only after the cleanup listing.
        self.assertEqual(zmx.calls[0][0], "run")
        self.assertEqual(zmx.calls[-2], ["kill", zmx.sentinel, "--force"])
        self.assertEqual(result, {"passed": True})  # untouched: main-path result is written by the try body

    def test_blind_listing_with_exit_zero_fails_cleanup(self):
        cleanup, result = self.run_finally(FakeZmx(self, blind=True))
        ident = self.assert_identity(cleanup)
        self.assertFalse(ident["positive_control"]["listed_before_teardown"])
        self.assertTrue(all(e["exit_code"] == 0 and e["stdout_rows"] == 0 and e["stderr_bytes"] > 0
                            for e in ident["zmx_list"]))
        self.assertFalse(result["passed"])
        self.assert_identity(result)

    def test_nonzero_list_exit_is_recorded_and_fails_cleanup(self):
        cleanup, _ = self.run_finally(FakeZmx(self, list_exit=1))
        ident = self.assert_identity(cleanup)
        self.assertEqual(ident["zmx_list"][0]["exit_code"], 1)
        self.assertGreater(ident["zmx_list"][0]["stderr_bytes"], 0)
        self.assertFalse(ident["positive_control"]["passed"])

    def test_sentinel_lost_during_teardown_fails_cleanup(self):
        cleanup, _ = self.run_finally(FakeZmx(self, lose_sentinel=True))
        control = self.assert_identity(cleanup)["positive_control"]
        self.assertTrue(control["listed_before_teardown"])
        self.assertFalse(control["listed_after_teardown"])
        self.assertIn("after teardown", control["error"])

    def test_unconfirmed_sentinel_kill_fails_cleanup(self):
        cleanup, _ = self.run_finally(FakeZmx(self, sentinel_unconfirmed=True))
        control = self.assert_identity(cleanup)["positive_control"]
        self.assertTrue(control["listed_after_teardown"])
        self.assertFalse(control["killed_confirmed"])
        self.assertFalse(control["passed"])


class IdentityHelperTests(unittest.TestCase):
    def setUp(self):
        self.ns = namespace(identity={"app_processes": []})
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_harness_identity_matches_git_blob_id(self):
        record = self.ns["harness_identity"](str(SOURCE), "c" * 40, None)
        self.assertEqual(record["sha256"], hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        self.assertIsNone(record["matches_commit"])
        if shutil.which("git"):
            blob = subprocess.check_output(["git", "hash-object", str(SOURCE)], text=True).strip()
            self.assertEqual(record["git_blob"], blob)
        self.assertTrue(self.ns["harness_identity"](str(SOURCE), "c" * 40, record["git_blob"])["matches_commit"])
        self.assertFalse(self.ns["harness_identity"](str(SOURCE), "c" * 40, "0" * 40)["matches_commit"])

    def test_missing_harness_is_reported_not_hashed(self):
        record = self.ns["harness_identity"](str(self.root / "absent.sh"), "", "")
        self.assertEqual((record["sha256"], record["git_commit"], record["error"]), (None, None, "FileNotFoundError"))

    def test_executable_identity_kinds_and_no_absolute_paths(self):
        elf, script = self.root / "app", self.root / "wrapper"
        elf.write_bytes(b"\x7fELF" + b"\0" * 60)
        script.write_text("#!/bin/sh\nexec app\n")
        link = self.root / "agterm"
        link.symlink_to(elf)
        for path, kind in ((elf, "elf"), (script, "script"), (link, "elf")):
            record = self.ns["executable_identity"](str(path))
            self.assertEqual(record["kind"], kind)
            self.assertRegex(record["sha256"], SHA256)
            self.assertNotIn(str(self.root), json.dumps(record))
        self.assertEqual(self.ns["executable_identity"](str(link))["resolved_name"], "app")
        missing = self.ns["executable_identity"](str(self.root / "gone"))
        self.assertEqual((missing["sha256"], missing["error"]), (None, "FileNotFoundError"))

    def test_note_app_process_hashes_the_running_image(self):
        process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(5)"])
        try:
            self.ns["note_app_process"](process)
        finally:
            process.kill()
            process.wait()
        entry = self.ns["identity"]["app_processes"][0]
        self.assertEqual(entry["launch"], 1)
        if Path("/proc/self/exe").exists():
            self.assertEqual(entry["sha256"], hashlib.sha256(Path(os.path.realpath(sys.executable)).read_bytes()).hexdigest())
        else:  # no procfs (macOS): recorded as unavailable, never guessed
            self.assertIsNone(entry["sha256"])
            self.assertIn("error", entry)


@unittest.skipUnless(os.access(os.environ.get("AGTERM_IDENTITY_TEST_ZMX", ""), os.X_OK),
                     "AGTERM_IDENTITY_TEST_ZMX not set to a real zmx executable")
class RealZmxPositiveControlTests(unittest.TestCase):
    """Positive and negative control against a real zmx; socket paths must stay short (sun_path)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zi-", dir="/tmp")
        self.zmx = os.path.realpath(os.environ["AGTERM_IDENTITY_TEST_ZMX"])
        self.socket_dir = str(Path(self.directory.name) / "s")
        os.mkdir(self.socket_dir)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.kill_leftovers)
        self.ns = namespace(identity={"zmx_list": [], "positive_control": None},
                            env={k: v for k, v in os.environ.items() if not k.startswith("ZMX_")},
                            live_endpoint={"executable": self.zmx, "socketDirectory": self.socket_dir},
                            live_daemons={OWNED})

    def kill_leftovers(self):
        control = self.ns["identity"]["positive_control"] or {}
        if control.get("name"):
            subprocess.run([self.zmx, "kill", control["name"], "--force"], capture_output=True, timeout=5,
                           env=dict(self.ns["env"], ZMX_DIR=self.socket_dir))

    def test_real_sentinel_is_listed_and_removed(self):
        sentinel = self.ns["start_zmx_sentinel"]()
        self.assertTrue(self.ns["identity"]["positive_control"]["listed_before_teardown"],
                        self.ns["identity"]["positive_control"])
        self.assertTrue(self.ns["cleanup_live_daemons"](sentinel), self.ns["identity"])
        self.assertTrue(self.ns["identity"]["positive_control"]["passed"])

    def test_real_listing_of_wrong_directory_fails(self):
        sentinel = self.ns["start_zmx_sentinel"]()
        wrong = str(Path(self.directory.name) / "w")
        os.mkdir(wrong)
        self.ns["live_endpoint"] = {"executable": self.zmx, "socketDirectory": wrong}
        try:
            self.assertFalse(self.ns["cleanup_live_daemons"](sentinel))
        finally:
            self.ns["live_endpoint"] = {"executable": self.zmx, "socketDirectory": self.socket_dir}
        wrong_lists = [e for e in self.ns["identity"]["zmx_list"] if e["stdout_rows"] == 0]
        self.assertTrue(wrong_lists and all(e["exit_code"] == 0 for e in wrong_lists), self.ns["identity"])


if __name__ == "__main__":
    unittest.main()
