#!/usr/bin/env bash
# Exercise the real Wayland frontend on an existing Hyprland test desktop.
# AGTERM_TEST_BIN / AGTERM_TEST_CTL may point at a staged or installed build.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AGTERM_TEST_BIN="${AGTERM_TEST_BIN:-$ROOT/agterm-linux/.build/debug/AgtermLinux}"
export AGTERM_TEST_CTL="${AGTERM_TEST_CTL:-$ROOT/agterm-linux/.build/debug/agtermctl-linux}"
export AGTERM_GHOSTTY_RESOURCES="${AGTERM_GHOSTTY_RESOURCES:-$ROOT/agterm-linux/vendor/ghostty/share/ghostty}"
export AGTERM_TEST_RESTORE_MODE="${AGTERM_TEST_RESTORE_MODE:-none}"
export AGTERM_OMARCHY_ARTIFACTS="${AGTERM_OMARCHY_ARTIFACTS:-$ROOT/artifacts/omarchy-$(date +%Y%m%d-%H%M%S)}"
exec python3 - <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid

for command in ("hyprctl", "grim", "magick"):
    if not shutil.which(command):
        raise SystemExit(f"missing test dependency: {command}")
if not os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
    raise SystemExit("run inside the Omarchy Hyprland session (WAYLAND_DISPLAY and HYPRLAND_INSTANCE_SIGNATURE required)")
for key in ("AGTERM_TEST_BIN", "AGTERM_TEST_CTL"):
    if not os.access(os.environ[key], os.X_OK):
        raise SystemExit(f"not executable: {os.environ[key]}")

artifacts = Path(os.environ["AGTERM_OMARCHY_ARTIFACTS"]).resolve()
if artifacts.exists() and any(artifacts.iterdir()):
    raise SystemExit(f"artifact directory must be empty: {artifacts}")
artifacts.mkdir(parents=True, exist_ok=True)
restore_mode = os.environ["AGTERM_TEST_RESTORE_MODE"]
if restore_mode not in ("none", "live"):
    raise SystemExit("AGTERM_TEST_RESTORE_MODE must be none or live")
run = Path(tempfile.mkdtemp(prefix="agt-wl-", dir="/tmp"))
state = run / "state"
home = run / "home"
state.mkdir()
home.mkdir()
(state / "settings.json").write_text(json.dumps({
    "welcomeShown": True, "restoreMode": restore_mode, "closeGraceUndoEnabled": False,
    "notificationsEnabled": False,
}))
env = {key: value for key, value in os.environ.items()
       if not key.startswith("AGTERM_") and key not in ("GDK_DEBUG", "GDK_DISABLE", "GTK_DEBUG", "GSK_DEBUG")}
env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
           XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"),
           XDG_STATE_HOME=str(home / ".local/state"),
           AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"),
           AGTERM_APP_ID="io.github.melonamin.agterm.smoke.r" + uuid.uuid4().hex,
           GDK_BACKEND="wayland", XDG_SESSION_TYPE="wayland")
# Keep the real compositor runtime directory and session bus; isolate app state and registration.
for key in ("AGTERM_GHOSTTY_RESOURCES", "AGTERM_RESOURCE_ROOT", "AGTERM_ZMX_PATH"):
    if key in os.environ:
        env[key] = os.environ[key]
app = None
checks = []
transcript = artifacts / "control.jsonl"
lua_dispatch = False
live_endpoint = None
live_daemons = set()


def command(argv, **kwargs):
    return subprocess.check_output(argv, text=True, timeout=15, **kwargs)


def ctl(*args):
    response = subprocess.run([os.environ["AGTERM_TEST_CTL"], *args,
                               "--socket", env["AGTERM_CONTROL_SOCKET"], "--json"],
                              env=env, text=True, capture_output=True, timeout=15)
    result = json.loads(response.stdout)
    with transcript.open("a") as out:
        out.write(json.dumps({"args": args, "response": result}) + "\n")
    if response.returncode or not result.get("ok"):
        raise AssertionError(f"control command failed: {args}: {result}")
    return result.get("result", {})


def wait_for(test, reason, seconds=20):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        if app and app.poll() is not None:
            raise AssertionError(f"app exited with {app.returncode}: {reason}")
        try:
            value = test()
            if value:
                return value
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            last_error = error
        time.sleep(0.2)
    raise AssertionError(f"timed out: {reason}; last error: {last_error}")


def tree():
    return ctl("tree")["tree"]


def sessions():
    return [session for workspace in tree()["workspaces"] for session in workspace["sessions"]]


def session(session_id):
    return next((s for s in sessions() if s["id"] == session_id), {})


def client():
    clients = json.loads(command(["hyprctl", "clients", "-j"]))
    return next((c for c in clients if c.get("pid") == app.pid and c.get("mapped")), None)


def capture(name):
    global lua_dispatch
    window = wait_for(client, "test window mapped in Hyprland")
    if window.get("xwayland"):
        raise AssertionError("test window is using XWayland, not native Wayland")
    address = window["address"]
    if not re.fullmatch(r"0x[0-9a-fA-F]+", address):
        raise AssertionError(f"invalid compositor window address: {address}")
    if not lua_dispatch:
        focus = subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
                               text=True, capture_output=True, timeout=15)
        if focus.returncode:
            if "dispatch in lua" not in focus.stdout + focus.stderr:
                raise AssertionError(f"focus failed: {focus.stdout}{focus.stderr}")
            lua_dispatch = True
    if lua_dispatch:
        command(["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{address}" }})'])
    wait_for(lambda: json.loads(command(["hyprctl", "activewindow", "-j"])).get("pid") == app.pid,
             "test window focused for capture")
    x, y = window["at"]
    width, height = window["size"]
    if width <= 0 or height <= 0:
        raise AssertionError("test window has no drawable area")
    path = artifacts / f"{name}.png"
    subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", str(path)], check=True, timeout=15)
    return path


def pixels(path):
    data = subprocess.check_output(["magick", str(path), "-alpha", "off", "-depth", "8", "rgb:-"], timeout=15)
    red = green = 0
    for offset in range(0, len(data), 3):
        r, g, b = data[offset:offset + 3]
        red += r > 150 and g < 80 and b < 100
        green += g > 150 and r < 80 and b < 100
    return red, green


def shell(session_id, text, pane="left"):
    if restore_mode == "live":
        remember_live_daemons()
    marker = run / ("executed-" + uuid.uuid4().hex)
    ctl("session", "type", text + "; printf done > " + shlex.quote(str(marker)) + "\n",
        "--target", session_id, "--pane", pane)
    wait_for(marker.exists, f"shell executed input in {pane} pane")


def remember_live_daemons():
    global live_endpoint
    inventory = ctl("zmx", "list")["zmx"]
    endpoint = inventory["endpoint"]
    assert re.fullmatch(r"/tmp/agterm-zmx-[0-9a-f]{16}", endpoint["socketDirectory"])
    assert Path(endpoint["executable"]).is_absolute()
    assert live_endpoint is None or live_endpoint == endpoint, "test zmx endpoint changed"
    live_endpoint = endpoint
    for entry in inventory["entries"]:
        if entry["state"] == "claimed":
            name = entry["daemon"]
            assert re.fullmatch(r"agterm-[0-9a-f]{32}", name)
            live_daemons.add(name)


def cleanup_live_daemons():
    """Use the saved isolated endpoint even if restarting the frontend failed."""
    if not live_endpoint or not live_daemons:
        return True
    cleanup_env = dict(env, ZMX_DIR=live_endpoint["socketDirectory"])
    for key in ("ZMX_SESSION", "ZMX_SESSION_PREFIX"):
        cleanup_env.pop(key, None)
    def invoke(*args):
        return subprocess.run([live_endpoint["executable"], *args], env=cleanup_env,
                              text=True, capture_output=True, timeout=5, check=True)
    def present():
        names = set()
        for line in invoke("list").stdout.splitlines():
            line = line.strip().removeprefix("→ ")
            fields = line.split("\t")
            matches = [field.removeprefix("name=") for field in fields if field.startswith("name=")]
            if len(matches) != 1 or not matches[0]:
                raise RuntimeError("unrecognized zmx list row; refusing to assume cleanup succeeded")
            names.add(matches[0])
        return names
    try:
        for name in sorted(live_daemons & present()):
            killed = invoke("kill", name, "--force")
            lines = (killed.stdout + killed.stderr).splitlines()
            if f"killed session {name}" not in (line.strip() for line in lines):
                raise RuntimeError("zmx did not confirm the test daemon was killed")
        if live_daemons & present():
            raise RuntimeError("test-owned zmx daemons remain after cleanup")
        return True
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"isolated zmx cleanup failed: {error}")
        return False


try:
    with (artifacts / "app.log").open("w") as log:
        app = subprocess.Popen([os.environ["AGTERM_TEST_BIN"]], env=env, stdout=log, stderr=log,
                               start_new_session=True)
    wait_for(lambda: Path(env["AGTERM_CONTROL_SOCKET"]).is_socket(), "isolated control socket")
    wait_for(client, "Hyprland window")
    initial = wait_for(sessions, "initial terminal session")[0]["id"]
    wait_for(lambda: session(initial).get("realized"), "initial terminal GL surface ready")
    # An execution marker proves PTY input reached a shell, not just its echoed command line.
    shell(initial, "printf 'OMARCHY_PRIMARY_OK\\n'")
    assert "OMARCHY_PRIMARY_OK" in ctl("session", "text", "--target", initial)["text"]
    checks.append("primary PTY execution and screen read-back")
    if restore_mode == "live":
        assert session(initial).get("backedByZmx"), (
            "Live mode did not launch a zmx-backed shell: " + json.dumps(ctl("restore", "mode"))
        )
        checks.append("Live mode shell execution through bundled zmx")
        before = run / "shell-pid-before"
        after = run / "shell-pid-after"
        shell(initial, "printf '%s' \"$$\" > " + shlex.quote(str(before)))
        # SIGTERM is the app's clean shutdown path; Live mode must preserve the shell daemon.
        app.terminate()
        app.wait(timeout=15)
        with (artifacts / "app.log").open("a") as log:
            app = subprocess.Popen([os.environ["AGTERM_TEST_BIN"]], env=env, stdout=log, stderr=log,
                                   start_new_session=True)
        wait_for(lambda: Path(env["AGTERM_CONTROL_SOCKET"]).is_socket(), "restarted control socket")
        wait_for(lambda: session(initial).get("realized"), "restored Live terminal GL surface ready")
        shell(initial, "printf '%s' \"$$\" > " + shlex.quote(str(after)))
        assert before.read_text() == after.read_text(), "Live restart replaced the shell process"
        checks.append("Live session preserves shell PID across application restart")
    for name, color in (("red", "230;20;30"), ("green", "20;230;30")):
        shell(initial, "printf '\\033[2J\\033[H\\033[48;2;" + color +
              "m'; for i in {1..8}; do printf '%60s\\n' ' '; done; printf '\\033[0m'")
        def colored_frame():
            counts = pixels(capture("terminal-" + name))
            return counts if counts[0 if name == "red" else 1] > 4000 else None
        counts = wait_for(colored_frame, f"{name} terminal pixels rendered by libghostty")
        if name == "red":
            first = counts
        else:
            assert first[0] > counts[0] + 3000 and counts[1] > first[1] + 3000, (first, counts)
    checks.append("native Wayland rendering: red-to-green PTY pixel change")
    workspace = ctl("workspace", "new", "Omarchy smoke")["id"]
    second = ctl("session", "new", "--workspace", workspace, "--name", "Omarchy split", "--cwd", str(home))["id"]
    ctl("session", "split", "on", "--target", second)
    wait_for(lambda: session(second).get("split"), "split state read-back")
    shell(second, "printf 'OMARCHY_SPLIT_OK\\n'", "right")
    assert "OMARCHY_SPLIT_OK" in ctl("session", "text", "--target", second, "--pane", "right")["text"]
    capture("split")
    checks.append("workspace/session creation and split PTY execution")
    ctl("session", "overlay", "open", "printf 'OMARCHY_OVERLAY_OK\\n'; sleep 30", "--target", second)
    wait_for(lambda: "OMARCHY_OVERLAY_OK" in ctl("session", "overlay", "text", "--target", second).get("text", ""),
             "program overlay output")
    capture("overlay")
    ctl("session", "overlay", "close", "--target", second)
    shell(second, "printf 'OMARCHY_AFTER_OVERLAY_OK\\n'", "right")
    checks.append("overlay output, dismissal, and restored split input")
    html = run / "smoke.html"
    html.write_text("<!doctype html><title>Omarchy WebKit smoke</title><h1>OMARCHY_HTML_OK</h1>")
    ctl("session", "overlay", "open", "--html", str(html), "--target", second)
    wait_for(lambda: any(page.get("state") == "loaded" for page in session(second).get("htmlOverlays", [])),
             "WebKitGTK file overlay loaded")
    capture("html-overlay")
    ctl("session", "overlay", "close", "--target", second)
    checks.append("WebKitGTK HTML overlay load")
    ctl("session", "split", "off", "--target", second)
    ctl("session", "close", "--target", second)
    ctl("workspace", "delete", "--target", workspace)
    assert all(w["id"] != workspace for w in tree()["workspaces"])
    checks.append("split/session/workspace teardown")
    (artifacts / "final-tree.json").write_text(json.dumps(tree(), indent=2))
    log = (artifacts / "app.log").read_text()
    for failure in ("GtkGLArea failed to create a GL context", "GLArea re-realized over a live surface",
                    "Theme parser error: <data>", "Theme parser warning: <data>"):
        assert failure not in log, failure
    checks.append("no terminal GL-context or app CSS failures")
    (artifacts / "result.json").write_text(json.dumps({
        "passed": True, "restore_mode": restore_mode, "checks": checks,
    }, indent=2))
    print("PASS: " + "; ".join(checks))
finally:
    if app and app.poll() is None:
        try:
            for item in sessions():
                ctl("session", "close", "--target", item["id"])
        except Exception as error:
            print(f"cleanup control failure: {error}")
        if app.poll() is None:
            app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
            app.wait(timeout=5)
    cleanup_ok = cleanup_live_daemons()
    (artifacts / "cleanup.json").write_text(json.dumps({
        "passed": cleanup_ok, "tracked_daemon_count": len(live_daemons),
    }, indent=2))
    if not cleanup_ok:
        (artifacts / "result.json").write_text(json.dumps({
            "passed": False, "cleanup_passed": False, "completed_checks": checks,
        }, indent=2))
    elif not (artifacts / "result.json").exists():
        (artifacts / "result.json").write_text(json.dumps({"passed": False, "completed_checks": checks}, indent=2))
    shutil.copytree(state, artifacts / "state", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.sock", "*.lock"))
    if cleanup_ok:
        shutil.rmtree(run)
    else:
        print(f"Retained test state for recovery: {run}")
    print(f"Artifacts: {artifacts}")
    if not cleanup_ok:
        raise AssertionError("isolated Live-session cleanup failed; state retained")
PY
