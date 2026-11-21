"""Exercise the GTK retry policy and window-focus ordering without a display or PyGObject."""

import ast
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


TESTS = Path(__file__).resolve().parents[2] / "agterm-linux/tests"
SPEC = importlib.util.spec_from_file_location("atspi_retry", TESTS / "atspi_retry.py")
retry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retry)

COMMAND = ["test-scenario"]
MENU_FAILURE = "missing real GUI button: Move to…"


def failed(command, message=MENU_FAILURE, kind="AssertionError", status=1):
    return subprocess.CompletedProcess(command, status, f"{kind}: {message}\nFAIL: {message}\n")


class RetryTests(unittest.TestCase):
    def run_case(self, results, *, arch="aarch64", scenario="v0430-gui", github="true"):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(AGTERM_ATSPI_SCENARIO=scenario, GITHUB_ACTIONS=github,
                       AGTERM_UI_ARTIFACT_DIR=directory, AGTERM_UI_APP_STDERR="shared.log")
            output = io.StringIO()
            with (patch.object(retry.platform, "machine", return_value=arch),
                  patch.object(retry.subprocess, "run", side_effect=results) as process,
                  redirect_stdout(output)):
                status = retry.run_scenario(COMMAND, env)
            calls = process.call_args_list
            for call in calls:
                self.assertEqual(call.args, (COMMAND,))
                self.assertEqual(call.kwargs["env"]["AGTERM_UI_APP_STDERR"], "shared.log")
            paths = [Path(call.kwargs["env"]["AGTERM_UI_ARTIFACT_DIR"]).relative_to(directory)
                     for call in calls]
            return status, paths, output.getvalue()

    def test_scoped_failure_retries_once_and_keeps_both_attempts(self):
        status, paths, output = self.run_case([
            failed(COMMAND), subprocess.CompletedProcess(COMMAND, 0, "PASS: v0430-gui\n")])
        self.assertEqual(status, (0, 1))
        self.assertEqual(paths, [Path("v0430-gui/attempt-1"), Path("v0430-gui/attempt-2")])
        self.assertLess(output.index(f"FAIL: {MENU_FAILURE}"), output.index("AT-SPI retry:"))
        self.assertIn("attempt=2/2", output)
        self.assertIn("PASS: v0430-gui", output)
        self.assertIn("AT-SPI retry result: arch=aarch64 scenario=v0430-gui attempt=2/2 status=0", output)

    def test_second_failure_is_final_even_if_retryable(self):
        status, paths, output = self.run_case([failed(COMMAND), failed(COMMAND)])
        self.assertEqual(status, (1, 1))
        self.assertEqual(len(paths), 2)
        self.assertEqual(output.count(f"FAIL: {MENU_FAILURE}"), 2)
        self.assertIn("attempt=2/2 status=1", output)

    def test_requires_exact_architecture_scenario_host_status_and_assertion(self):
        for options, failure in (
            ({"arch": "x86_64"}, failed(COMMAND)),
            ({"scenario": "leader-repeat"}, failed(COMMAND)),
            ({"github": "false"}, failed(COMMAND)),
            ({}, failed(COMMAND, f"{MENU_FAILURE} elsewhere")),
            ({}, failed(COMMAND, kind="RuntimeError")),
            ({}, failed(COMMAND, status=-11)),
            ({}, subprocess.CompletedProcess(COMMAND, 1, f"AssertionError: {MENU_FAILURE}\n"
                                                        f"FAIL: {MENU_FAILURE}\ncleanup failed\n")),
        ):
            with self.subTest(options=options, failure=failure):
                status, paths, output = self.run_case([failure], **options)
                self.assertEqual(status, (failure.returncode, 0))
                self.assertEqual(len(paths), 1)
                self.assertNotIn("AT-SPI retry", output)

    def test_success_stays_single_attempt(self):
        status, paths, output = self.run_case([subprocess.CompletedProcess(COMMAND, 0, "PASS: v0430-gui\n")])
        self.assertEqual(status, (0, 0))
        self.assertEqual(len(paths), 1)
        self.assertEqual(output, "PASS: v0430-gui\n")


class LeaderFocusTests(unittest.TestCase):
    def test_repeat_tail_goes_to_destination_after_delayed_x11_focus_transfer(self):
        # Run the real scenario call site with focus held on the old window for 300 ms.
        # A fixed 100 ms delay sends 'b' to the old window and cannot write the marker.
        source = TESTS / "atspi_leader_v0351.py"
        tree = ast.parse(source.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        clock = 0.0
        switch_at = None
        windows = [{"id": "first", "open": True, "active": True}]
        marker = None

        def focused():
            if len(windows) == 1 or switch_at is not None and clock >= switch_at:
                return "101"
            return "102"

        def sleep(seconds):
            nonlocal clock
            clock += float(seconds)

        def wait_for(predicate, message):
            deadline = clock + 12
            while clock < deadline:
                if result := predicate():
                    return result
                sleep(0.1)
            raise AssertionError(message)

        def keys(command, **kwargs):
            nonlocal switch_at
            args = command[1:]
            if args[:2] == ["keydown", "a"]:
                marker.write_text("aab")
            elif args[:2] == ["key", "w"]:
                windows[0]["active"], windows[1]["active"] = True, False
                switch_at = clock + 0.3
                if "sleep" in args:
                    sleep(args[args.index("sleep") + 1])
                    if focused() == "101":
                        marker.write_text(marker.read_text() + "b")
            elif args == ["key", "b"] and focused() == "101":
                marker.write_text(marker.read_text() + "b")
            elif args == ["key", "a", "key", "Escape", "key", "b"]:
                marker.write_text(marker.read_text() + "a")

        def control(env, request):
            if request["cmd"] == "window.new":
                windows[0]["active"] = False
                windows.append({"id": "second", "open": True, "active": True})
            if request["cmd"] == "keymap.run":
                marker.write_text(marker.read_text() + "b")
            return {"ok": True}

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory, "repeat.marker")
            namespace = dict(os=os, shlex=shlex,
                             subprocess=SimpleNamespace(run=keys, DEVNULL=None),
                             time=SimpleNamespace(sleep=sleep),
                             launch=lambda env: (SimpleNamespace(pid=404), None),
                             focus_window=lambda pid: None, stop=lambda process: None,
                             window_list=lambda env: windows, raw_control_json=control, wait_for=wait_for)
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
            namespace["focused_x11_window"] = focused
            with redirect_stdout(io.StringIO()):
                namespace["verify_leader_repeat"]({"AGTERM_STATE_DIR": directory})
