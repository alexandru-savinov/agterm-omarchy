"""Exercise the real reconnect predicates and fixture logging without GTK or an app."""
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "agterm-linux/tests/atspi_reconnect_v0351.py"
spec = importlib.util.spec_from_file_location("reconnect_diagnostics", SOURCE)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def session(failures=1, reason="ssh: connect to host: Connection refused", recovering=False):
    return {"surfaces": [dict(kind=kind, paneID=kind + "-id", lead="follower",
                             **({} if recovering else {"reconnect": {"failures": failures, "reason": reason}}))
                         for kind in ("left", "right")]}


class ReconnectTests(unittest.TestCase):
    def exercise(self, waiting, recovered=None):
        with tempfile.TemporaryDirectory() as directory:
            down = str(Path(directory) / "ssh-down")
            Path(down + ".processes.log").write_text("101 term-child-status=143 exit=255\n")
            values = iter([session(recovering=True), waiting, waiting,
                           recovered or session(recovering=True), recovered or session(recovering=True)])
            output = io.StringIO()
            calls = []
            real_open = open

            def fixture_open(path, *args, **kwargs):
                if str(path).startswith("/proc/"):
                    if str(path).endswith("cmdline"):
                        return io.BytesIO(b"/fixture/ssh\0'attach'\0")
                    return io.BytesIO(b"AGTERM_TEST_ORIGIN_STATE=/fixture/origin\0SECRET=never-print\0")
                return real_open(path, *args, **kwargs)

            def wait_for(predicate, message, timeout=12, required=True):
                calls.append(timeout)
                if predicate():
                    return True
                if required:
                    raise AssertionError(message)
                return None

            error = None
            with patch.dict(sys.modules, {"atspi_smoke": types.SimpleNamespace(wait_for=wait_for)}), \
                    patch.object(helper.os, "listdir", return_value=["101", "102"]), \
                    patch.object(helper.os, "kill") as kill, \
                    patch.object(helper, "process_status", side_effect=lambda pid: {"pid": int(pid), "state": "S"}), \
                    patch("builtins.open", side_effect=fixture_open), contextlib.redirect_stdout(output):
                try:
                    helper.verify_pane_reconnect(lambda: next(values), "/fixture/ssh", down, "/fixture/origin")
                except AssertionError as caught:
                    error = caught
            self.assertEqual(kill.call_count, 2)
            self.assertNotIn("never-print", output.getvalue())
            return error, output.getvalue(), calls

    def test_success_keeps_both_semantic_checks_and_deadlines(self):
        error, output, calls = self.exercise(session())
        self.assertIsNone(error)
        self.assertEqual(calls, [25, 30])
        self.assertNotIn("RECONNECT_DIAGNOSTICS", output)

    def test_zero_failure_count_still_fails_and_retains_snapshot(self):
        error, output, calls = self.exercise(session(failures=0))
        self.assertIn("failed-probe reason/count", str(error))
        report = json.loads(output.split("RECONNECT_DIAGNOSTICS ")[1])
        self.assertEqual(report["snapshots"][-1]["panes"][0]["reconnect"]["failures"], 0)
        self.assertIn("exit=255", report["fixture_events"])
        self.assertEqual(len(report["signalled"]), 2)
        self.assertEqual(calls, [25])

    def test_wrong_reason_still_fails(self):
        error, output, _ = self.exercise(session(reason="other failure"))
        self.assertIsNotNone(error)
        self.assertIn("other failure", output)

    def test_one_pane_without_failure_still_fails(self):
        value = session()
        value["surfaces"][1].pop("reconnect")
        error, _, _ = self.exercise(value)
        self.assertIsNotNone(error)

    def test_recovery_failure_is_reported(self):
        error, output, calls = self.exercise(session(), recovered=session())
        self.assertIsNotNone(error)
        self.assertIn('"phase": "recovery"', output)
        self.assertEqual(calls, [25, 30])

    def test_proc_stat_handles_spaces_and_parentheses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "101"
            path.mkdir()
            fields = ["S", "12"] + ["0"] * 48
            fields[19], fields[49] = "12345", "65280"
            (path / "stat").write_text("101 (name (with) spaces) " + " ".join(fields))
            self.assertEqual(helper.process_status(101, directory),
                             {"pid": 101, "state": "S", "ppid": 12, "start_ticks": "12345", "exit_code": 65280})
            self.assertTrue(helper.process_status(102, directory)["unavailable"])

    def fixture_script(self):
        tree = ast.parse((ROOT / "agterm-linux/tests/atspi_smoke.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "verify_remote_presentation")
        script = next(n.args[0].value for n in ast.walk(function) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == "write"
                      and n.args and isinstance(n.args[0], ast.Constant)
                      and isinstance(n.args[0].value, str) and n.args[0].value.startswith("#!/bin/sh"))
        return script

    def test_real_fixture_refusal_logs_255_and_preserves_stderr(self):
        script = self.fixture_script()
        with tempfile.TemporaryDirectory() as directory:
            down = Path(directory) / "down"
            down.touch()
            fixture = Path(directory) / "ssh"
            fixture.write_text(script)
            subprocess.run(["/bin/sh", "-n", str(fixture)], check=True)
            result = subprocess.run(["/bin/sh", str(fixture), "host", "true"],
                                    env={"AGTERM_TEST_SSH_DOWN": str(down)}, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 255)
            self.assertEqual(result.stderr, "ssh: connect to host: Connection refused\n")
            self.assertIn("refused exit=255", Path(str(down) + ".processes.log").read_text())

    def test_real_fixture_term_logs_child_status_and_exits_255(self):
        with tempfile.TemporaryDirectory() as directory:
            down = Path(directory) / "down"
            events = Path(str(down) + ".processes.log")
            fixture = Path(directory) / "ssh"
            fixture.write_text(self.fixture_script())
            with subprocess.Popen(["/bin/sh", str(fixture), "host", "exec /bin/sleep 10 # 'attach'"],
                                  env={"AGTERM_TEST_SSH_DOWN": str(down)},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
                try:
                    deadline = time.monotonic() + 3
                    ready = False
                    while time.monotonic() < deadline:
                        if events.exists():
                            records = events.read_text().splitlines(keepends=True)
                            ready = any(line.startswith(f"{process.pid} attach child=") and line.endswith("\n")
                                        for line in records)
                            if ready:
                                break
                        time.sleep(0.01)
                    self.assertTrue(ready, "fixture did not publish a complete post-trap attach record")
                    process.terminate()
                    self.assertEqual(process.wait(timeout=3), 255)
                    self.assertIn("term-child-status=143 exit=255", events.read_text())
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
