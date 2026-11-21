"""Exercise the GTK retry policy and window-focus ordering without a display or PyGObject."""

import ast
from contextlib import nullcontext, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "agterm-linux/tests"
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
            summary, outputs = Path(directory, "summary"), Path(directory, "outputs")
            env = dict(AGTERM_ATSPI_SCENARIO=scenario, GITHUB_ACTIONS=github,
                       AGTERM_UI_ARTIFACT_DIR=directory, AGTERM_UI_APP_STDERR="shared.log",
                       GITHUB_STEP_SUMMARY=str(summary), GITHUB_OUTPUT=str(outputs))
            output = io.StringIO()
            calls = []
            pending = iter(results)

            def run(command, env):
                self.assertEqual(command, COMMAND)
                self.assertEqual(env["AGTERM_UI_APP_STDERR"], "shared.log")
                calls.append(Path(env["AGTERM_UI_ARTIFACT_DIR"]).relative_to(directory))
                return next(pending)

            def spawn(command, env, **kwargs):
                self.assertEqual(env["PYTHONUNBUFFERED"], "1")
                self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
                if len(calls) == 1:
                    self.assertEqual(outputs.read_text(), "retried=true\n")
                result = run(command, env)
                trace = result.stdout

                def stream():
                    for line in trace.splitlines(keepends=True):
                        yield line
                        # Every line must be visible before the consumer asks for more output.
                        self.assertTrue(output.getvalue().endswith(line))

                return nullcontext(SimpleNamespace(stdout=stream(), wait=lambda: result.returncode))

            with (patch.object(retry.platform, "machine", return_value=arch),
                  patch.object(retry.subprocess, "run", side_effect=run),
                  patch.object(retry.subprocess, "Popen", side_effect=spawn),
                  redirect_stdout(output)):
                status = retry.run_scenario(COMMAND, env)
            self.assertEqual(outputs.exists(), bool(status[1]))
            self.summary = summary.read_text() if summary.exists() else ""
            return status, calls, output.getvalue()

    def test_scoped_failure_retries_once_and_keeps_both_attempts(self):
        status, paths, output = self.run_case([
            failed(COMMAND), subprocess.CompletedProcess(COMMAND, 0, "PASS: v0430-gui\n")])
        self.assertEqual(status, (0, 1))
        self.assertEqual(paths, [Path("v0430-gui/attempt-1"), Path("v0430-gui/attempt-2")])
        self.assertLess(output.index(f"FAIL: {MENU_FAILURE}"), output.index("AT-SPI retry:"))
        self.assertIn("attempt=2/2", output)
        self.assertIn("PASS: v0430-gui", output)
        self.assertIn("AT-SPI retry result: arch=aarch64 scenario=v0430-gui attempt=2/2 status=0", output)
        self.assertIn(f"first_status=1 assertion={MENU_FAILURE!r}", self.summary)
        self.assertIn("- AT-SPI retry result: arch=aarch64 scenario=v0430-gui attempt=2/2 status=0", self.summary)

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

    def test_window_close_retry_matches_only_the_observed_arm_setup_failure(self):
        session = "0BDFAC92-4576-4727-BCA7-8191A84E33D3"
        message = f"session {session} never answered before"
        good = subprocess.CompletedProcess(COMMAND, 0, "PASS: window-close-cancel\n")
        args = dict(scenario="window-close-cancel")
        self.assertEqual(self.run_case([failed(COMMAND, message), good], **args)[0], (0, 1))
        for options, other in (({"arch": "x86_64"}, message), ({}, message.replace("before", "after")),
                               ({}, message.replace(session, "unexpected"))):
            with self.subTest(options=options, message=other):
                self.assertEqual(self.run_case([failed(COMMAND, other)], **args, **options)[0], (1, 0))

    def test_retry_policy_matches_documented_table(self):
        section = (ROOT / "docs/ci-flakes.md").read_text().split("## Omarchy GTK AT-SPI smoke suite (#527)\n", 1)[1]
        section = section.split("\n## ", 1)[0]
        rows = re.findall(r"^\| (\S+) \| `([^`]+)` — `([^`]+)` \|", section, re.MULTILINE)
        self.assertEqual(len(rows), len(retry.RETRYABLE))
        self.assertEqual({(arch, scenario): message for arch, scenario, message in rows}, retry.RETRYABLE)

    def test_workflow_uploads_diagnostics_after_recovery_and_failure(self):
        workflow = (ROOT / ".github/workflows/omarchy-ci.yml").read_text()
        smoke, upload = workflow.split("      - name: GTK AT-SPI smoke tests\n", 1)[1].split(
            "      - name: Upload GTK diagnostics on failure or retry\n", 1)
        self.assertIn("        id: gtk-smoke\n", smoke)
        self.assertTrue(upload.startswith("        if: failure() || steps.gtk-smoke.outputs.retried == 'true'\n"))


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
            if clock < 0.2:
                return "999"  # A window from a different application initially owns keyboard focus.
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
            if args == ["getwindowfocus"]:
                return subprocess.CompletedProcess(command, 0, focused() + "\n")
            if args[:1] == ["getwindowpid"]:
                pid = "800" if args[1] == "999" else "404"
                return subprocess.CompletedProcess(command, 0, pid + "\n")
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
            with redirect_stdout(io.StringIO()):
                namespace["verify_leader_repeat"]({"AGTERM_STATE_DIR": directory})
