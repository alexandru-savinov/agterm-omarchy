"""Real clipboard queries and protected paste must interpret the same bytes differently."""

import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, activate, control_json, launch, named,
                         press_x11_key, stop, wait_for, window_list)
from gi.repository import Gdk, GLib, GObject, Gtk

READ_HEADING = "Allow a program to read the clipboard?"
PASTE_HEADING = "Paste this text?"
READER = r'''
import os,pathlib,select,sys,termios,time,tty
fd=sys.stdin.fileno(); previous=termios.tcgetattr(fd); reply=b""
try:
    tty.setraw(fd)
    os.write(1,b"\x1b]52;c;?\x07")
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if not select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]: break
        chunk=os.read(fd,4096)
        if not chunk: break
        reply+=chunk
        pathlib.Path(sys.argv[1]).write_bytes(reply)
        if len(reply)>65536: raise RuntimeError("oversized clipboard reply")
        if reply.endswith(b"\x07") or reply.endswith(b"\x1b\\"): break
    pathlib.Path(sys.argv[1]).write_bytes(reply)
    pathlib.Path(sys.argv[2]).write_text("complete")
finally:
    termios.tcsetattr(fd,termios.TCSANOW,previous)
'''
COLLECTOR = r'''
import os,pathlib,select,sys,termios,tty
fd=sys.stdin.fileno(); previous=termios.tcgetattr(fd); data=b""
try:
    tty.setraw(fd)
    os.write(1,b"\x1b[?2004h" if sys.argv[3]=="true" else b"\x1b[?2004l")
    pathlib.Path(sys.argv[2]).write_bytes(data)
    pathlib.Path(sys.argv[1]).write_text("ready")
    while True:
        if not select.select([fd],[],[],1)[0]: continue
        chunk=os.read(fd,4096)
        if not chunk: break
        data+=chunk
        if len(data)>65536: raise RuntimeError("oversized paste input")
        pathlib.Path(sys.argv[2]).write_bytes(data)
finally:
    termios.tcsetattr(fd,termios.TCSANOW,previous)
'''


def verify_clipboard_payloads(env, state):
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", state)) / "clipboard-payloads"
    artifacts.mkdir(parents=True, exist_ok=True)
    Gtk.init()
    clipboard = Gdk.Display.get_default().get_clipboard()
    mixed = "agterm λ\r\nCRLF\rCR\nLF"
    uri = "file:///tmp/one%20two\r\nfile:///tmp/three\r\n"
    for label, bracketed, protection, trust_bracketed in (
            ("protected", False, True, True), ("bracketed", True, True, True),
            ("bracketed-asks", True, True, False), ("unprotected", False, False, True)):
        root = Path(state) / label
        root.mkdir()
        (root / "settings.json").write_text(json.dumps({"restoreMode": "none"}))
        (root / "config").mkdir()
        (root / "config/ghostty.conf").write_text(
            "clipboard-read = ask\nclipboard-paste-protection = " + str(protection).lower()
            + "\nclipboard-paste-bracketed-safe = " + str(trust_bracketed).lower() + "\n")
        reader, collector = root / "reader.py", root / "collector.py"
        reader.write_text(READER)
        collector.write_text(COLLECTOR)
        local = dict(env, AGTERM_STATE_DIR=str(root), AGTERM_CONTROL_SOCKET=str(root / "agterm.sock"))
        process, app = launch(local)
        drag_sources = []

        def pump():
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)

        def until(predicate, message, timeout=10):
            def served():
                pump()
                return predicate()
            return wait_for(served, message, timeout=timeout)

        def set_text(text):
            value = GObject.Value(GObject.TYPE_STRING)
            value.set_string(text)
            clipboard.set(value)
            assert clipboard.is_local(), "fixture clipboard ownership failed"

        def answer(heading, response):
            until(lambda: named(app, heading), f"{heading} did not appear")
            activate(until(lambda: named(app, response, role="button"), f"{response} button missing"))
            until(lambda: named(app, heading) is None, f"{heading} did not close")

        def start(command, name):
            result = control_json(local, "session", "new", "--name", name, "--window", window, "--json")
            assert result["ok"], result
            session = result["result"]["id"]
            result = control_json(local, "session", "type", shlex.join(command) + "\n",
                                  "--target", session, "--window", window, "--json")
            assert result["ok"], result
            return session

        def paste(text, drop=False):
            if not drop:
                press_x11_key("ctrl+shift+v", process.pid)
                return
            title = f"agterm clipboard drag fixture {len(drag_sources)}"
            source = Gtk.Window(title=title)
            drag_sources.append(source)
            source.set_default_size(200, 100)
            label = Gtk.Label(label="Drag fixture text")
            source.set_child(label)
            drag = Gtk.DragSource.new()
            drag.set_actions(Gdk.DragAction.COPY)
            value = GObject.Value(GObject.TYPE_STRING)
            value.set_string(text)
            provider = Gdk.ContentProvider.new_for_value(value)
            drag.connect("prepare", lambda *_args: provider)
            label.add_controller(drag)
            source.present()
            driver = None
            try:
                def source_window():
                    found = subprocess.run(["xdotool", "search", "--onlyvisible", "--name",
                                            "^" + title + "$"], text=True, capture_output=True)
                    return found.stdout.splitlines()[0] if found.returncode == 0 else None
                source_id = until(source_window, "drag source did not map")
                subprocess.run(["xdotool", "windowmove", "--sync", source_id, "20", "20"], check=True, timeout=10)
                target_id = subprocess.check_output(
                    ["xdotool", "search", "--onlyvisible", "--pid", str(process.pid)], text=True, timeout=10).splitlines()[-1]
                def center(window_id):
                    geometry = dict(line.split("=", 1) for line in subprocess.check_output(
                        ["xdotool", "getwindowgeometry", "--shell", window_id], text=True, timeout=10).splitlines())
                    return int(geometry["X"]) + int(geometry["WIDTH"]) // 2, int(geometry["Y"]) + int(geometry["HEIGHT"]) // 2
                sx, sy = center(source_id)
                tx, ty = center(target_id)
                driver = subprocess.Popen(["xdotool", "mousemove", "--sync", str(sx), str(sy), "mousedown", "1",
                                           "sleep", "0.2", "mousemove", "--sync", str(sx + 25), str(sy),
                                           "sleep", "0.2", "mousemove", "--sync", str(tx), str(ty),
                                           "sleep", "0.3", "mouseup", "1"])
                until(lambda: driver.poll() is not None, "drag driver did not finish")
                assert driver.returncode == 0, "drag input failed"
            finally:
                if driver is not None and driver.poll() is None:
                    driver.terminate()
                    driver.wait(timeout=5)
                pump()

        def remains_empty(output, reason):
            deadline = time.monotonic() + NEGATIVE_SETTLE_SECONDS
            while time.monotonic() < deadline:
                pump()
                assert process.poll() is None, "frontend exited during clipboard validation"
                assert output.read_bytes() == b"", reason
                time.sleep(0.05)

        try:
            window = until(lambda: next((row["id"] for row in window_list(local) if row.get("active")), None),
                           "clipboard test window did not become active")
            if label == "protected":
                for kind, payload in (("mixed", mixed), ("uri", uri)):
                    for response in ("Allow", "Deny"):
                        set_text(payload)
                        output = artifacts / f"{label}-read-{kind}-{response}.reply"
                        complete = artifacts / f"{label}-read-{kind}-{response}.complete"
                        start(["/usr/bin/python3", str(reader), str(output), str(complete)], f"read-{kind}-{response}")
                        until(lambda: named(app, READ_HEADING), "OSC52 read did not ask permission")
                        assert not output.exists(), "clipboard query completed before consent"
                        answer(READ_HEADING, response)
                        until(complete.exists, "clipboard query did not finish", timeout=25)
                        match = re.fullmatch(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)", output.read_bytes())
                        assert match, f"invalid OSC52 response: {output.read_bytes()!r}"
                        expected = payload.encode() if response == "Allow" else b""
                        actual = base64.b64decode(match[1], validate=True)
                        assert actual == expected, (
                            f"{kind} OSC52 {response} did not preserve the exact expected bytes: {actual!r} != {expected!r}")
                        print(f"OK: exact {kind} OSC52 {response}", flush=True)
            payloads = (("cr", "one\rtwo"), ("lf", "one\ntwo"))
            if label == "protected":
                payloads += (("uri", uri), ("drop-cr", "one\rtwo"), ("drop-lf", "one\ntwo"), ("drop-uri", uri))
            for kind, payload in payloads:
                ready, output = root / f"{kind}.ready", artifacts / f"{label}-{kind}.input"
                start(["/usr/bin/python3", str(collector), str(ready), str(output), str(bracketed).lower()],
                      f"paste-{label}-{kind}")
                until(ready.exists, "raw paste receiver did not start")
                set_text("clipboard-fallback-sentinel-313" if kind.startswith("drop-") else payload)
                paste(payload, drop=kind.startswith("drop-"))
                must_ask = protection and not (bracketed and trust_bracketed) and not kind.endswith("uri")
                if must_ask:
                    def unsafe_prompt():
                        assert output.read_bytes() == b"", f"{label}/{kind}: unsafe paste reached the PTY before consent"
                        return named(app, PASTE_HEADING)
                    until(unsafe_prompt, f"{label}/{kind}: unsafe paste was not confirmed")
                    answer(PASTE_HEADING, "Cancel")
                    remains_empty(output, "cancelled paste delivered bytes")
                    paste(payload, drop=kind.startswith("drop-"))
                    answer(PASTE_HEADING, "Paste")
                normalized = "/tmp/one\\ two /tmp/three" if kind.endswith("uri") else payload.replace("\r\n", "\n").replace("\r", "\n")
                expected = (("\x1b[200~" + normalized + "\x1b[201~") if bracketed
                            else normalized.replace("\n", "\r")).encode()
                until(lambda: output.read_bytes() == expected, f"{label}/{kind}: paste bytes differ from expected")
                assert named(app, PASTE_HEADING) is None, "paste left an unexpected confirmation dialog"
                print(f"OK: {label}/{kind} actual paste bytes; confirmation={must_ask}", flush=True)
        finally:
            stop(process)
            for source in drag_sources:
                source.close()
            pump()
    print("OK: exact OSC52 text and URI-looking bytes; protected paste cancellation/allow, bracketed and configured policy")
