"""Exercise both smoke restart call sites with a reply after the raw socket deadline."""

import ast
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest


TESTS = Path(__file__).resolve().parents[2] / "agterm-linux/tests"


class RestartTransportTests(unittest.TestCase):
    def run_probe(self, assignment, command_args):
        elapsed = 12  # Simulated reply time, within the CLI's 30-second request budget.
        socket_path = "/test/restart.sock"
        env = {"AGTERM_CONTROL_SOCKET": socket_path}
        reply = {"ok": True, "result": {"restart": {"oldPid": 100, "newPid": 101}}}

        def raw_control_json(environment, request, timeout=10):
            if elapsed > timeout:
                raise TimeoutError("timed out waiting for the restart reply")
            return reply

        def check_output(argv, *, env, text, timeout):
            if elapsed > timeout:
                raise subprocess.TimeoutExpired(argv, timeout)
            self.assertEqual(env["AGTERM_CONTROL_SOCKET"], socket_path)
            self.assertTrue(text)
            self.assertEqual(timeout, 35)
            self.assertEqual(argv, ["agtermctl-linux", "session", "restart", "--target", "session-id",
                                    "--pane-id", "pane-id", "--window", "window-id", "--json",
                                    *command_args, "--socket", socket_path])
            return json.dumps(reply)

        scope = dict(env=env, session="session-id", identity="pane-id", window="window-id",
                     line="echo 'restart marker'", CTL="agtermctl-linux", json=json,
                     raw_control_json=raw_control_json,
                     subprocess=SimpleNamespace(check_output=check_output,
                                                CalledProcessError=subprocess.CalledProcessError))
        smoke_source = TESTS / "atspi_smoke.py"
        smoke = ast.parse(smoke_source.read_text())
        control = next(node for node in smoke.body if isinstance(node, ast.FunctionDef) and node.name == "control_json")
        exec(compile(ast.Module(body=[control], type_ignores=[]), str(smoke_source), "exec"), scope)

        restart_source = TESTS / "atspi_restart_v0351.py"
        module = ast.parse(restart_source.read_text())
        scenario = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef) and node.name == "verify_restart_v0351")
        probe = next(node for node in ast.walk(scenario) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == assignment for target in node.targets))
        helpers = [node for node in ast.walk(scenario)
                   if isinstance(node, ast.FunctionDef) and node.name in ("request", "restart")]
        exec(compile(ast.Module(body=[*helpers, probe], type_ignores=[]), str(restart_source), "exec"), scope)
        self.assertEqual(scope[assignment], reply)

    def test_explicit_command_waits_for_the_real_client(self):
        self.run_probe("reply", ["--command", "echo 'restart marker'"])

    def test_replay_waits_for_the_real_client(self):
        self.run_probe("replay", [])
