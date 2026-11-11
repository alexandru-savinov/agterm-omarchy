"""Real PTY OSC 52 reads across consent and requester teardown."""

import base64
import json
from pathlib import Path
import re
import shlex
import time

from gi.repository import Gdk, GLib, GObject, Gtk

from atspi_clipboard_lifetime import read_clipboard
from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, activate, control_json, launch, named,
                         stop, wait_for, window_list, window_sessions)


HEADING = "Allow a program to read the clipboard?"
READER = r'''
import os, pathlib, select, sys, termios, time, tty
fd = sys.stdin.fileno()
previous = termios.tcgetattr(fd)
reply = b""
try:
    tty.setraw(fd)
    os.write(sys.stdout.fileno(), b"\x1b]52;c;?\x07")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        reply += chunk
        if len(reply) > 65536:
            raise RuntimeError("oversized clipboard response")
        if reply.endswith(b"\x07") or reply.endswith(b"\x1b\\"):
            break
    pathlib.Path(sys.argv[1]).write_bytes(reply)
finally:
    termios.tcsetattr(fd, termios.TCSANOW, previous)
'''


def verify_clipboard_read_lifetime(env):
    state = Path(env["AGTERM_STATE_DIR"])
    (state / "settings.json").write_text(json.dumps({"restoreMode": "none", "closeGraceUndoEnabled": False}))
    (state / "config").mkdir(exist_ok=True)
    (state / "config/ghostty.conf").write_text("clipboard-read = ask\n")
    reader = state / "clipboard-reader.py"
    reader.write_text(READER)
    Gtk.init()
    clipboard = Gdk.Display.get_default().get_clipboard()
    sentinel = "agterm read fixture: spaces, apostrophe ' and unicode λ"
    value = GObject.Value(GObject.TYPE_STRING)
    value.set_string(sentinel)
    clipboard.set(value)
    assert read_clipboard(clipboard) == sentinel
    process, app = launch(env)

    def read_consent():
        # This process owns the fixture clipboard and must serve the application's async read.
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        return named(app, HEADING)

    def request(window, label):
        response = control_json(env, "session", "new", "--name", label, "--window", window, "--json")
        assert response["ok"], response
        session = response["result"]["id"]
        output = state / f"{label}.reply"
        command = shlex.join(["/usr/bin/python3", str(reader), str(output)]) + "\n"
        response = control_json(env, "session", "type", command, "--target", session, "--window", window, "--json")
        assert response["ok"], response
        wait_for(read_consent, "OSC 52 read did not present consent")
        assert not output.exists(), "read completed before consent was answered"
        return session, output

    def respond(label):
        dialog = wait_for(lambda: named(app, HEADING), "read consent disappeared before response")
        activate(wait_for(lambda: named(dialog, label, role="button"), "read consent response missing"))
        wait_for(lambda: named(app, HEADING) is None, "read consent remained after response")

    def reply_bytes(output):
        wait_for(output.exists, "PTY reader did not finish", timeout=25)
        match = re.fullmatch(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)", output.read_bytes())
        assert match is not None, f"unexpected OSC 52 reply: {output.read_bytes()!r}"
        return base64.b64decode(match.group(1), validate=True)

    try:
        window = wait_for(lambda: next((item["id"] for item in window_list(env) if item.get("active")), None),
                          "initial window did not become active")
        stale, _ = request(window, "read-before-close")
        response = control_json(env, "session", "close", "--target", stale, "--window", window, "--json")
        assert response["ok"], response
        wait_for(lambda: all(item["id"] != stale for item in window_sessions(env, window)),
                 "read requester did not close")
        respond("Allow")
        deadline = time.monotonic() + NEGATIVE_SETTLE_SECONDS
        while time.monotonic() < deadline:
            assert process.poll() is None, "frontend exited after consent for a closed read requester"
            assert read_clipboard(clipboard) == sentinel, "read consent changed clipboard contents"
            time.sleep(0.1)

        _, allowed = request(window, "read-live-allow")
        respond("Allow")
        assert reply_bytes(allowed) == sentinel.encode(), "live read did not deliver exact fixture bytes"
        _, denied = request(window, "read-live-deny")
        respond("Deny")
        assert reply_bytes(denied) == b"", "denied read disclosed clipboard content"
        assert process.poll() is None, "frontend exited during read recovery"
        print("OK: late read consent after requester close remains usable; live Allow returns exact bytes and Deny returns empty")
    finally:
        stop(process)
