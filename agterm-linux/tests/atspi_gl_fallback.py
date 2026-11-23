"""A GL context failure on the default renderer relaunches agterm once in Mesa software rendering."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from atspi_launcher_environment import make_debug_payload
from atspi_smoke import (BIN, app_stderr_sink, control_json, find_app, named_prefix, stop, wait_for,
                         window_list)

NOTICE = "OpenGL failed with the default renderer, so agterm restarted with software rendering."
OVERLAY = "Terminal rendering needs OpenGL."
RELAUNCH_LINE = "agterm: GL context failed with the default renderer; restarting once with LIBGL_ALWAYS_SOFTWARE=1"
GUARD = "AGTERM_GL_SOFTWARE_FALLBACK"
PROBE = r'''
import json,os,pathlib,sys
names=("LIBGL_ALWAYS_SOFTWARE","AGTERM_GL_SOFTWARE_FALLBACK")
pathlib.Path(sys.argv[1]).write_text(json.dumps({n:os.environ.get(n) for n in names}))
'''


def proc_environ(pid):
    raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    return dict(entry.decode(errors="replace").split("=", 1) for entry in raw if b"=" in entry)


def visible(pid, prefix):
    return named_prefix(find_app(pid), prefix) is not None


def start(env, binary, label, artifacts):
    """The runner's shared stderr log keeps its gates on this app; the returned reader sees only this launch."""
    shared = os.environ.get("AGTERM_UI_APP_STDERR")
    path = Path(shared) if shared else artifacts / f"{label}.stderr"
    offset = path.stat().st_size if path.exists() else 0
    sink = app_stderr_sink() if shared else open(path, "ab")
    try:
        process = subprocess.Popen([str(binary)], env=env, stdout=subprocess.DEVNULL, stderr=sink)
    finally:
        sink.close()
    return process, lambda: path.read_bytes()[offset:].decode(errors="replace").splitlines()


def leg_env(env, state, **extra):
    local = {key: value for key, value in env.items()
             if key not in ("LIBGL_ALWAYS_SOFTWARE", GUARD, "AGTERM_DISABLE_GL_FALLBACK", "LD_PRELOAD")
             and not key.startswith("AGTERM_ORIG_")}
    (state / "settings.json").write_text(json.dumps({"restoreMode": "none", "closeGraceUndoEnabled": False}))
    local.update(AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"), **extra)
    return local


def verify_recovers_through_launcher(env, artifacts):
    with tempfile.TemporaryDirectory(prefix="glfb-", dir="/tmp") as temporary:
        state = Path(temporary)
        payload = state / "payload"
        launcher = make_debug_payload(payload, env["AGTERM_GHOSTTY_RESOURCES"])
        (payload / "share/agterm/RENDERER").write_text("default\n")
        local = leg_env(env, state, AGTERM_ATSPI_SURFACE_FAILURE="gl-context-hardware:main")
        process, log = start(local, launcher, "recover", artifacts)
        try:
            wait_for(lambda: proc_environ(process.pid).get(GUARD) == "1", "default-renderer GL failure did not relaunch")
            app_env = proc_environ(process.pid)
            assert app_env.get("LIBGL_ALWAYS_SOFTWARE") == "1", app_env.get("LIBGL_ALWAYS_SOFTWARE")
            assert "libagterm-egl-compat.so" in Path(f"/proc/{process.pid}/maps").read_text(), \
                "the relaunch dropped the launcher's EGL shim"
            wait_for(lambda: visible(process.pid, NOTICE), "software-rendering notice was not shown")
            assert not visible(process.pid, OVERLAY), "the relaunched image still showed the GL-error overlay"
            window = wait_for(lambda: next((w["id"] for w in window_list(local) if w["open"]), None),
                              "relaunched window missing")
            output = state / "probe.json"
            probe = state / "probe.py"
            probe.write_text(PROBE)
            created = control_json(local, "session", "new", "--name", "glfb", "--window", window, "--json")
            assert created["ok"], created
            command = shlex.join(["/usr/bin/python3", str(probe), str(output)]) + "\n"
            for _ in range(12):
                control_json(local, "session", "type", command, "--target", created["result"]["id"],
                             "--window", window, "--json")
                if wait_for(output.exists, "", timeout=1, required=False):
                    break
            assert output.exists(), "relaunched pane never ran"
            child = json.loads(output.read_text())
            assert child == {"LIBGL_ALWAYS_SOFTWARE": None, GUARD: None}, child
            lines = log()
            assert lines.count(RELAUNCH_LINE) == 1, lines
            print("OK: default-renderer GL failure relaunched once in software rendering with a notice", flush=True)
        finally:
            stop(process)


def verify_overlay_without_relaunch(env, artifacts, label, failure, expect_relaunches, **extra):
    with tempfile.TemporaryDirectory(prefix="glfb-", dir="/tmp") as temporary:
        local = leg_env(env, Path(temporary), AGTERM_ATSPI_SURFACE_FAILURE=failure, **extra)
        process, log = start(local, BIN, label, artifacts)
        try:
            wait_for(lambda: visible(process.pid, OVERLAY), f"{label}: GL-error overlay missing")
            assert process.poll() is None, f"{label}: app exited"
            lines = log()
            assert lines.count(RELAUNCH_LINE) == expect_relaunches, (label, lines)
            assert (proc_environ(process.pid).get(GUARD) == "1") == bool(expect_relaunches), label
            print(f"OK: {label}: GL-error overlay after {expect_relaunches} relaunch(es)", flush=True)
        finally:
            stop(process)


def verify_gl_software_fallback(env):
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", env["AGTERM_STATE_DIR"])) / "gl-fallback"
    artifacts.mkdir(parents=True, exist_ok=True)
    verify_recovers_through_launcher(env, artifacts)
    # A failure that software rendering does not cure stops after exactly one relaunch.
    verify_overlay_without_relaunch(env, artifacts, "no-loop", "gl-context:main", 1)
    verify_overlay_without_relaunch(env, artifacts, "disabled", "gl-context-hardware:main", 0,
                                    AGTERM_DISABLE_GL_FALLBACK="1")
