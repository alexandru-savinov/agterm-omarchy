"""Real clipboard queries and protected paste must interpret the same bytes differently."""

import base64
import json
import os
from pathlib import Path
import re
import shlex
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
        if len(reply)>65536: raise RuntimeError("oversized clipboard reply")
        if reply.endswith(b"\x07") or reply.endswith(b"\x1b\\"): break
    pathlib.Path(sys.argv[1]).write_bytes(reply)
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
                        output = root / f"read-{kind}-{response}.reply"
                        start(["/usr/bin/python3", str(reader), str(output)], f"read-{kind}-{response}")
                        until(lambda: named(app, READ_HEADING), "OSC52 read did not ask permission")
                        assert not output.exists(), "clipboard query completed before consent"
                        answer(READ_HEADING, response)
                        until(output.exists, "clipboard query did not finish", timeout=25)
                        match = re.fullmatch(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)", output.read_bytes())
                        assert match, f"invalid OSC52 response: {output.read_bytes()!r}"
                        expected = payload.encode() if response == "Allow" else b""
                        assert base64.b64decode(match[1], validate=True) == expected, (
                            f"{kind} OSC52 {response} did not preserve the exact expected bytes")
                        print(f"OK: exact {kind} OSC52 {response}", flush=True)
            payloads = (("cr", "one\rtwo"), ("lf", "one\ntwo"))
            if label == "protected":
                payloads += (("uri", uri),)
            for kind, payload in payloads:
                ready, output = root / f"{kind}.ready", root / f"{kind}.input"
                start(["/usr/bin/python3", str(collector), str(ready), str(output), str(bracketed).lower()],
                      f"paste-{label}-{kind}")
                until(ready.exists, "raw paste receiver did not start")
                set_text(payload)
                press_x11_key("ctrl+shift+v", process.pid)
                must_ask = protection and not (bracketed and trust_bracketed) and kind != "uri"
                if must_ask:
                    until(lambda: named(app, PASTE_HEADING), f"{label}/{kind}: unsafe paste was not confirmed")
                    assert output.read_bytes() == b"", "unsafe paste reached the PTY before consent"
                    answer(PASTE_HEADING, "Cancel")
                    remains_empty(output, "cancelled paste delivered bytes")
                    press_x11_key("ctrl+shift+v", process.pid)
                    answer(PASTE_HEADING, "Paste")
                normalized = "/tmp/one\\ two /tmp/three" if kind == "uri" else payload.replace("\r\n", "\n").replace("\r", "\n")
                expected = (("\x1b[200~" + normalized + "\x1b[201~") if bracketed
                            else normalized.replace("\n", "\r")).encode()
                until(lambda: output.read_bytes() == expected, f"{label}/{kind}: paste bytes differ from expected")
                assert named(app, PASTE_HEADING) is None, "paste left an unexpected confirmation dialog"
                print(f"OK: {label}/{kind} actual paste bytes; confirmation={must_ask}", flush=True)
        finally:
            stop(process)
    print("OK: exact OSC52 text and URI-looking bytes; protected paste cancellation/allow, bracketed and configured policy")
