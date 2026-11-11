"""Observe presence as well as values in real ordinary and freshly created zmx panes."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from atspi_smoke import ROOT, control_json, launch, raw_control_json, stop, wait_for, window_list, window_tree


PROBE = r'''
import json,os,pathlib,sys
names=("LD_LIBRARY_PATH","LIBGL_ALWAYS_SOFTWARE","AGTERM_COMMIT","AGTERM_VERSION",
       "AGTERM_ZMX_PATH","AGTERM_GHOSTTY_RESOURCES","GSK_RENDERER")
result={"values":{name:os.environ.get(name) for name in names},
        "bookkeeping":sorted(name for name in os.environ if name.startswith("AGTERM_ORIG_"))}
path=pathlib.Path(sys.argv[1]); temporary=path.with_suffix(".tmp")
temporary.write_text(json.dumps(result)); temporary.replace(path)
print("launcher270-probe-complete",flush=True)
'''


def verify_exact_launcher_environment(env):
    base = Path(env["AGTERM_STATE_DIR"])
    fixture = base / "launcher-login-shell.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(fixture),
                    os.path.join(ROOT, "tests/login_shell_zsh_fixture.c"), "-ldl"], check=True)
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", base)) / "launcher-environment"
    artifacts.mkdir(parents=True, exist_ok=True)
    names = ("LD_LIBRARY_PATH", "LIBGL_ALWAYS_SOFTWARE", "AGTERM_COMMIT", "AGTERM_VERSION",
             "AGTERM_ZMX_PATH", "AGTERM_GHOSTTY_RESOURCES")
    for mode in ("none", "live"):
        for label, original in (("absent", None), ("empty", ""), ("value", "caller-value")):
            temporary = tempfile.TemporaryDirectory(prefix="env270-", dir="/tmp")
            state = Path(temporary.name)
            (state / "settings.json").write_text(json.dumps({"restoreMode": mode, "closeGraceUndoEnabled": False}))
            local = {key: value for key, value in env.items() if not key.startswith("AGTERM_ORIG_")}
            local.update(AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"),
                         LD_PRELOAD=str(fixture), LD_LIBRARY_PATH="/nonexistent/agterm-bundle/lib",
                         LIBGL_ALWAYS_SOFTWARE="1", AGTERM_COMMIT="launcher-app-commit",
                         AGTERM_VERSION="270.0", AGTERM_ZMX_PATH=os.path.join(ROOT, "vendor/zmx/zmx"),
                         AGTERM_GHOSTTY_RESOURCES=env["AGTERM_GHOSTTY_RESOURCES"],
                         AGTERM_ORIG_SET="1", GSK_RENDERER="cairo")
            local["AGTERM_ORIG_UNSET"] = " ".join(names) if original is None else ""
            if original is not None:
                for name in names:
                    local["AGTERM_ORIG_" + name] = original
            output = artifacts / f"{mode}-{label}.json"
            output.unlink(missing_ok=True)
            probe = state / "probe.py"
            probe.write_text(PROBE)
            process = None
            window = None
            try:
                process, _app = launch(local)
                window = wait_for(lambda: next((item["id"] for item in window_list(local) if item["open"]), None),
                                  "launcher matrix window missing")
                created = control_json(local, "session", "new", "--name", "launcher270", "--window", window, "--json")
                assert created["ok"], created
                session = created["result"]["id"]
                command = shlex.join(["/usr/bin/python3", str(probe), str(output)]) + "\n"
                for _ in range(12):
                    sent = control_json(local, "session", "type", command, "--target", session,
                                        "--window", window, "--json")
                    assert sent["ok"], sent
                    if wait_for(output.exists, "", timeout=1, required=False):
                        break
                assert output.exists(), f"{mode}/{label}: real pane never reported its environment"
                actual = json.loads(output.read_text())
                expected = {name: original for name in names}
                expected["GSK_RENDERER"] = "cairo"
                assert actual == {"values": expected, "bookkeeping": []}, (mode, label, actual, expected)
                # Read only the selected app variables, not arbitrary inherited process data.
                app_env = dict(entry.split(b"=", 1) for entry in Path(f"/proc/{process.pid}/environ").read_bytes().split(b"\0")
                               if b"=" in entry)
                for name in (*names, "AGTERM_ORIG_SET", "GSK_RENDERER"):
                    assert app_env[name.encode()] == local[name].encode(), f"app environment changed: {name}"
                if mode == "live":
                    rows = [row for workspace in window_tree(local, window)["workspaces"] for row in workspace["sessions"]]
                    row = next(row for row in rows if row["id"] == session)
                    identity = next(pane["paneID"] for pane in row["surfaces"] if pane["kind"] == "left")
                    daemon = "agterm-" + identity.replace("-", "").lower()
                    def daemon_screen():
                        result = raw_control_json(local, {"cmd": "zmx.screen", "args": {"name": daemon, "all": True}})
                        return result.get("ok") and "launcher270-probe-complete" in result["result"]["text"]
                    wait_for(daemon_screen, "environment probe did not run in the actual new zmx daemon")
                print(f"OK: exact {mode}/{label} pane environment, absent bookkeeping, unchanged app environment", flush=True)
            finally:
                try:
                    if window is not None and process.poll() is None:
                        rows = [row for workspace in window_tree(local, window)["workspaces"] for row in workspace["sessions"]]
                        for row in rows:
                            closed = control_json(local, "session", "close", row["id"], "--window", window, "--json")
                            assert closed["ok"], closed
                finally:
                    try:
                        if process is not None:
                            stop(process)
                    finally:
                        temporary.cleanup()
