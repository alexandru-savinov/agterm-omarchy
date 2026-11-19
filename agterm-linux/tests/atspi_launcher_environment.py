"""Verify the packaged launcher's environment at each real child launch boundary."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from atspi_smoke import (BIN, REPO, ROOT, control_json, launch, raw_control_json,
                         run_palette_action, stop, wait_for, window_list, window_tree)


PROBE = r'''
import json,os,pathlib,sys
names=("LD_PRELOAD","LD_LIBRARY_PATH","LIBGL_ALWAYS_SOFTWARE","AGTERM_COMMIT","AGTERM_VERSION",
       "AGTERM_ZMX_PATH","AGTERM_GHOSTTY_RESOURCES","GSK_RENDERER")
result={"values":{name:os.environ.get(name) for name in names},
        "bookkeeping":sorted(name for name in os.environ if name.startswith("AGTERM_ORIG_"))}
if len(sys.argv)>2: result["uri"]=sys.argv[2]
path=pathlib.Path(sys.argv[1]); temporary=path.with_suffix(f".{os.getpid()}.tmp")
temporary.write_text(json.dumps(result)); temporary.replace(path)
print("launcher270-probe-complete",flush=True)
'''


def make_debug_payload(root, ghostty_resources):
    # Use the staged launcher around the Debug app so GIO callback instrumentation is
    # available. Copy the executable so $ORIGIN resolves to this fixture.
    bin_dir = root / "bin"
    lib = root / "lib"
    share = root / "share"
    for directory in (bin_dir, lib, share / "agterm"):
        directory.mkdir(parents=True)
    stage = (Path(REPO) / "scripts/stage-linux.sh").read_text()
    launcher_text = stage.split("<<'LAUNCH'\n", 1)[1].split("\nLAUNCH\n", 1)[0]
    launcher = bin_dir / "agterm-linux"
    launcher.write_text(launcher_text + "\n")
    launcher.chmod(0o755)
    binary = Path(BIN).resolve()
    shutil.copy2(binary, bin_dir / "agterm-linux.bin")
    bundle = "agterm-linux_AgtermLinux.resources"
    (bin_dir / bundle).symlink_to(binary.parent / bundle, target_is_directory=True)
    (bin_dir / "zmx").symlink_to(Path(ROOT) / "vendor/zmx/zmx")
    (share / "ghostty").symlink_to(ghostty_resources, target_is_directory=True)
    for name, value in (("COMMIT", "launcher-app-commit"), ("VERSION", "270.0"), ("RENDERER", "software")):
        (share / "agterm" / name).write_text(value + "\n")
    subprocess.run(["cc", "-shared", "-fPIC", "-fvisibility=hidden", "-Wl,-z,defs",
                    "-o", str(lib / "libagterm-egl-compat.so"),
                    str(Path(REPO) / "linux/egl-compat.c")], check=True)
    return launcher


def verify_exact_launcher_environment(env):
    base = Path(env["AGTERM_STATE_DIR"])
    fixture = base / "launcher-login-shell.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(fixture),
                    os.path.join(ROOT, "tests/login_shell_zsh_fixture.c"), "-ldl"], check=True)
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", base)) / "launcher-environment"
    artifacts.mkdir(parents=True, exist_ok=True)
    payload = base / "launcher-payload"
    launcher = make_debug_payload(payload, env["AGTERM_GHOSTTY_RESOURCES"])
    names = ("LD_LIBRARY_PATH", "LIBGL_ALWAYS_SOFTWARE", "AGTERM_COMMIT", "AGTERM_VERSION",
             "AGTERM_ZMX_PATH", "AGTERM_GHOSTTY_RESOURCES")
    for mode in ("none", "live"):
        for label, original in (("absent", None), ("empty", ""), ("value", "caller-value")):
            temporary = tempfile.TemporaryDirectory(prefix="env270-", dir="/tmp")
            state = Path(temporary.name)
            (state / "settings.json").write_text(json.dumps({"restoreMode": mode, "closeGraceUndoEnabled": False}))
            local = {key: value for key, value in env.items()
                     if not key.startswith("AGTERM_ORIG_") and key not in
                     (*names, "LD_PRELOAD", "AGTERM_DISABLE_EGL_COMPAT")}
            local.update(AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"),
                         GSK_RENDERER="cairo")
            if original is not None:
                local.update({name: original for name in names})
            # The Live fixture selects zsh even if the test account uses another shell.
            preload = str(fixture) if mode == "live" or label == "value" else original
            if preload is not None:
                local["LD_PRELOAD"] = preload
            output = artifacts / f"{mode}-{label}.json"
            output.unlink(missing_ok=True)
            probe = state / "probe.py"
            probe.write_text(PROBE)
            route_outputs = {}
            if mode == "none":
                route_outputs = {route: artifacts / f"{route}-{label}.json"
                                 for route in ("hook", "custom", "gio")}
                for path in route_outputs.values():
                    path.unlink(missing_ok=True)
                config = state / "config"
                config.mkdir()
                custom = shlex.join(["/usr/bin/python3", str(probe), str(route_outputs["custom"])])
                (config / "keymap.conf").write_text(f'command "Launcher Environment" {custom}\n')
                applications = state / "xdg-data/applications"
                applications.mkdir(parents=True)
                xdg_config = state / "xdg-config"
                xdg_config.mkdir()
                uri = f"https://example.test/launcher270/{label}"
                callback = state / "url-callback"
                (applications / "agterm-env270.desktop").write_text(
                    "[Desktop Entry]\nType=Application\nName=Launcher environment probe\n"
                    f'Exec=/usr/bin/python3 "{probe}" "{route_outputs["gio"]}" %u\n'
                    "MimeType=x-scheme-handler/https;\nNoDisplay=true\n")
                (xdg_config / "mimeapps.list").write_text(
                    "[Default Applications]\nx-scheme-handler/https=agterm-env270.desktop;\n")
                local.update(XDG_DATA_HOME=str(applications.parent), XDG_CONFIG_HOME=str(xdg_config),
                             AGTERM_ATSPI_OPEN_URL=uri, AGTERM_ATSPI_URL_CAPTURE=str(callback))
            process = None
            window = None
            try:
                process, app = launch(local, binary=launcher)
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
                expected.update(LD_PRELOAD=preload, GSK_RENDERER="cairo")
                assert actual == {"values": expected, "bookkeeping": []}, (mode, label, actual, expected)
                if route_outputs:
                    hook = shlex.join(["/usr/bin/python3", str(probe), str(route_outputs["hook"])])
                    (config / "hooks.conf").write_text(f"on status {hook}\n")
                    reloaded = raw_control_json(local, {"cmd": "hooks.reload"})
                    assert reloaded["ok"], reloaded
                    status = raw_control_json(local, {"cmd": "session.status", "target": session,
                                                      "args": {"status": "active"}})
                    assert status["ok"], status
                    run_palette_action(app, process.pid, "launcher270", "Launcher Environment", badge="custom")
                    wait_for(callback.exists, "actual OPEN_URL callback did not reach the launch boundary")
                    assert callback.read_text() == uri
                    for route, path in route_outputs.items():
                        wait_for(path.exists, f"{label}/{route}: real child never reported its environment")
                        actual_child = json.loads(path.read_text())
                        expected_child = {"values": expected, "bookkeeping": []}
                        if route == "gio":
                            expected_child["uri"] = uri
                        assert actual_child == expected_child, (label, route, actual_child, expected_child)
                        print(f"OK: exact {label}/{route} child environment and absent bookkeeping", flush=True)
                # /proc/environ captures exec-time values. Require a mapped copy as
                # well, since the loader can leave the variable set on load failure.
                app_env = dict(entry.split(b"=", 1) for entry in Path(f"/proc/{process.pid}/environ").read_bytes().split(b"\0")
                               if b"=" in entry)
                shim = "$ORIGIN/../lib/libagterm-egl-compat.so"
                assert app_env[b"LD_PRELOAD"].decode() == (f"{shim}:{preload}" if preload else shim)
                assert "libagterm-egl-compat.so" in Path(f"/proc/{process.pid}/maps").read_text()
                app_values = {
                    "LD_LIBRARY_PATH": str(payload / "lib") + (f":{original}" if original else ""),
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                    "AGTERM_COMMIT": "launcher-app-commit",
                    "AGTERM_VERSION": "270.0",
                    "AGTERM_ZMX_PATH": str(payload / "bin/zmx"),
                    "AGTERM_GHOSTTY_RESOURCES": str(payload / "share/ghostty"),
                    "GSK_RENDERER": "cairo",
                    "AGTERM_ORIG_SET": "1",
                }
                for name, value in app_values.items():
                    assert app_env[name.encode()] == value.encode(), f"unexpected app environment: {name}"
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
                            closed = control_json(local, "session", "close", "--target", row["id"],
                                                  "--window", window, "--json")
                            assert closed["ok"], closed
                finally:
                    try:
                        if process is not None:
                            stop(process)
                    finally:
                        temporary.cleanup()
