"""Compositor-neutral launch probe for a staged agterm payload (ultra_arm64_runtime, #64).

It mirrors the launch path of scripts/test-omarchy.sh at fa64d44f (isolated HOME/state,
restoreMode none or live, initial session, PTY execution marker, Live zmx backing and shell
PID preservation across a SIGTERM restart) but needs no hyprctl/grim/magick, so it runs
under any Wayland compositor. It is NOT the acceptance harness: Hyprland-dependent checks
(window lookup, capture, red/green pixels) are out of its scope and reported NOT RUN.

Environment: PROBE_BIN, PROBE_CTL (executables), PROBE_MODE (none|live), PROBE_OUT (empty dir),
optional PROBE_LABEL. Writes result.json, app.log, control.jsonl, cleanup.json, state/.
"""
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

BIN = os.environ["PROBE_BIN"]
CTL = os.environ["PROBE_CTL"]
MODE = os.environ.get("PROBE_MODE", "none")
LABEL = os.environ.get("PROBE_LABEL", MODE)
OUT = Path(os.environ["PROBE_OUT"]).resolve()
assert MODE in ("none", "live"), MODE
for path in (BIN, CTL):
    assert os.access(path, os.X_OK), f"not executable: {path}"
if not os.environ.get("WAYLAND_DISPLAY"):
    raise SystemExit("WAYLAND_DISPLAY required")
OUT.mkdir(parents=True, exist_ok=True)
assert not any(OUT.iterdir()), "output directory must be empty"

run = Path(tempfile.mkdtemp(prefix="agt-probe-", dir="/tmp"))
state, home = run / "state", run / "home"
state.mkdir()
home.mkdir()
(state / "settings.json").write_text(json.dumps({
    "welcomeShown": True, "restoreMode": MODE, "closeGraceUndoEnabled": False,
    "notificationsEnabled": False,
}))
env = {k: v for k, v in os.environ.items()
       if not k.startswith(("AGTERM_", "PROBE_")) and k not in ("GDK_DEBUG", "GDK_DISABLE", "GTK_DEBUG", "GSK_DEBUG")}
env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
           XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"),
           XDG_STATE_HOME=str(home / ".local/state"),
           AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"),
           AGTERM_APP_ID="io.github.melonamin.agterm.smoke.r" + uuid.uuid4().hex,
           GDK_BACKEND="wayland", XDG_SESSION_TYPE="wayland", SHELL="/bin/bash")
transcript = OUT / "control.jsonl"
app = None
checks, timeline = [], []
endpoint, daemons = None, set()
started = time.monotonic()


def note(event, **extra):
    timeline.append({"t": round(time.monotonic() - started, 2), "event": event, **extra})


def ctl(*args):
    response = subprocess.run([CTL, *args, "--socket", env["AGTERM_CONTROL_SOCKET"], "--json"],
                              env=env, text=True, capture_output=True, timeout=15)
    result = json.loads(response.stdout)
    with transcript.open("a") as out:
        out.write(json.dumps({"args": args, "response": result}) + "\n")
    if response.returncode or not result.get("ok"):
        raise AssertionError(f"control command failed: {args}: {result}")
    return result.get("result", {})


def wait_for(test, reason, seconds=20):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        if app and app.poll() is not None:
            raise AssertionError(f"app exited with {app.returncode}: {reason}")
        try:
            value = test()
            if value:
                note("ok: " + reason)
                return value
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            last = error
        time.sleep(0.2)
    raise AssertionError(f"timed out: {reason}; last error: {last}")


def sessions():
    return [s for w in ctl("tree")["tree"]["workspaces"] for s in w["sessions"]]


def session(sid):
    return next((s for s in sessions() if s["id"] == sid), {})


def remember():
    global endpoint
    inventory = ctl("zmx", "list")["zmx"]
    endpoint = inventory["endpoint"]
    for entry in inventory["entries"]:
        if entry["state"] == "claimed":
            daemons.add(entry["daemon"])


def shell(sid, text):
    if MODE == "live":
        remember()
    marker = run / ("executed-" + uuid.uuid4().hex)
    ctl("session", "type", text + "; printf done > " + shlex.quote(str(marker)) + "\n",
        "--target", sid, "--pane", "left")
    wait_for(marker.exists, "shell executed input")


def launch():
    global app
    with (OUT / "app.log").open("a") as log:
        app = subprocess.Popen([BIN], env=env, stdout=log, stderr=log, start_new_session=True)
    note("launched", pid=app.pid)


def cleanup_daemons():
    if not endpoint or not daemons:
        return True, "no tracked daemons"
    cenv = dict(env, ZMX_DIR=endpoint["socketDirectory"])
    for key in ("ZMX_SESSION", "ZMX_SESSION_PREFIX"):
        cenv.pop(key, None)
    try:
        for name in sorted(daemons):
            subprocess.run([endpoint["executable"], "kill", name, "--force"], env=cenv,
                           text=True, capture_output=True, timeout=10)
        left = subprocess.run([endpoint["executable"], "list"], env=cenv, text=True,
                              capture_output=True, timeout=10).stdout
        remaining = [n for n in daemons if n in left]
        return not remaining, f"remaining={remaining}"
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)


failure = None
try:
    launch()
    wait_for(lambda: Path(env["AGTERM_CONTROL_SOCKET"]).is_socket(), "isolated control socket")
    checks.append("control socket")
    initial = wait_for(sessions, "initial terminal session")[0]["id"]
    checks.append("initial terminal session")
    wait_for(lambda: session(initial).get("realized"), "initial terminal GL surface ready")
    checks.append("initial GL surface realized")
    shell(initial, "printf 'OMARCHY_PRIMARY_OK\\n'")
    assert "OMARCHY_PRIMARY_OK" in ctl("session", "text", "--target", initial)["text"]
    checks.append("primary PTY execution and screen read-back")
    if MODE == "live":
        assert session(initial).get("backedByZmx"), "Live mode did not launch a zmx-backed shell"
        checks.append("Live mode shell execution through bundled zmx")
        before, after = run / "pid-before", run / "pid-after"
        shell(initial, "printf '%s' \"$$\" > " + shlex.quote(str(before)))
        app.terminate()
        app.wait(timeout=15)
        note("terminated for restart", code=app.returncode)
        launch()
        wait_for(lambda: Path(env["AGTERM_CONTROL_SOCKET"]).is_socket(), "restarted control socket")
        wait_for(lambda: session(initial).get("realized"), "restored Live terminal GL surface ready")
        shell(initial, "printf '%s' \"$$\" > " + shlex.quote(str(after)))
        assert before.read_text() == after.read_text(), "Live restart replaced the shell process"
        checks.append("Live session preserves shell PID across application restart")
except Exception as error:  # record, then clean up; the result is data
    failure = f"{type(error).__name__}: {error}"
    note("failure", detail=failure)
finally:
    if app and app.poll() is None:
        try:
            for item in sessions():
                ctl("session", "close", "--target", item["id"])
        except Exception as error:
            note("cleanup control failure", detail=str(error))
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
            app.wait(timeout=5)
    exit_code = app.returncode if app else None
    cleanup_ok, cleanup_detail = cleanup_daemons()
    (OUT / "cleanup.json").write_text(json.dumps({
        "passed": cleanup_ok, "tracked_daemon_count": len(daemons), "detail": cleanup_detail}, indent=2))
    shutil.copytree(state, OUT / "state", dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.sock", "*.lock"))
    log = (OUT / "app.log").read_text(errors="replace") if (OUT / "app.log").exists() else ""
    (OUT / "result.json").write_text(json.dumps({
        "probe": "live-probe.py (compositor-neutral; not scripts/test-omarchy.sh)",
        "label": LABEL, "restore_mode": MODE, "passed": failure is None and cleanup_ok,
        "failure": failure, "checks": checks, "timeline": timeline,
        "last_app_exit_code": exit_code, "zmx_endpoint": endpoint,
        "app_log_lines": log.count("\n"),
        "app_log_markers": {m: log.count(m) for m in ("zmx", "SIGILL", "Illegal instruction", "CRITICAL", "Fontconfig")},
        "hyprland_dependent_checks": "NOT RUN (window lookup, grim capture, red/green pixels)",
    }, indent=2))
    shutil.rmtree(run, ignore_errors=True)
    print(f"[{LABEL}] passed={failure is None and cleanup_ok} checks={checks} failure={failure}")
