"""Real PTY OSC 52 reads across consent and requester teardown."""

import base64
import json
from pathlib import Path
import re
import shlex
import time

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

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


class HeldClipboard(Gdk.ContentProvider):
    """Observe a real transfer request, then finish it only when the fixture releases it."""

    def __init__(self, text):
        super().__init__()
        self.delegate = Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(text.encode()))
        self.pending = []
        self.completed = 0
        self.failures = []
        self.holding = True

    def do_ref_formats(self):
        return self.delegate.ref_formats()

    def do_write_mime_type_async(self, mime, stream, priority, cancellable, callback, user_data=None):
        # Preserve the C caller's opaque data; Gio.Task's Python callback data is a different ABI layer.
        def deliver(source, result, _):
            callback(source, result, user_data)

        task = Gio.Task.new(self, cancellable, deliver, None)
        request = (mime, stream, priority, cancellable, task)
        if self.holding:
            self.pending.append(request)
        else:
            self.finish(request)

    def finish(self, request):
        mime, stream, priority, cancellable, task = request

        def completed(provider, result):
            try:
                task.return_boolean(provider.write_mime_type_finish(result))
            except GLib.Error as error:
                self.failures.append(str(error))
                task.return_error(error)
            self.completed += 1

        self.delegate.write_mime_type_async(mime, stream, priority, cancellable, completed)

    def do_write_mime_type_finish(self, result):
        return result.propagate_boolean()

    def release(self):
        self.holding = False
        pending, self.pending = self.pending, []
        for request in pending:
            self.finish(request)


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

    def pump():
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def read_consent():
        # This process owns the fixture clipboard and must serve the application's async read.
        pump()
        return named(app, HEADING)

    def start_request(window, label):
        response = control_json(env, "session", "new", "--name", label, "--window", window, "--json")
        assert response["ok"], response
        session = response["result"]["id"]
        output = state / f"{label}.reply"
        command = shlex.join(["/usr/bin/python3", str(reader), str(output)]) + "\n"
        response = control_json(env, "session", "type", command, "--target", session, "--window", window, "--json")
        assert response["ok"], response
        return session, output

    def request(window, label):
        session, output = start_request(window, label)
        wait_for(read_consent, "OSC 52 read did not present consent")
        assert not output.exists(), "read completed before consent was answered"
        return session, output

    def respond(label):
        wait_for(lambda: named(app, HEADING), "read consent disappeared before response")
        activate(wait_for(lambda: named(app, label, role="button"), "read consent response missing"))
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

        held = HeldClipboard(sentinel)
        assert clipboard.set_content(held)
        pending_session, _ = start_request(window, "read-transfer-close")

        def transfer_started():
            pump()
            return len(held.pending) == 1

        wait_for(transfer_started, "application never requested the held clipboard bytes")
        assert held.completed == 0 and named(app, HEADING) is None
        response = control_json(env, "session", "close", "--target", pending_session, "--window", window, "--json")
        assert response["ok"], response
        wait_for(lambda: all(item["id"] != pending_session for item in window_sessions(env, window)),
                 "requester with pending GDK read did not close")
        held.release()

        def transfer_completed():
            pump()
            return held.completed == 1

        wait_for(transfer_completed, "held clipboard transfer did not complete after release")
        assert not held.failures, f"held clipboard transfer failed: {held.failures}"
        deadline = time.monotonic() + NEGATIVE_SETTLE_SECONDS
        while time.monotonic() < deadline:
            assert read_consent() is None, "completed GDK read presented consent for a closed requester"
            assert process.poll() is None, "frontend exited after completing a closed requester's GDK read"
            time.sleep(0.1)

        _, allowed = request(window, "read-live-allow")
        respond("Allow")
        assert reply_bytes(allowed) == sentinel.encode(), "live read did not deliver exact fixture bytes"
        _, denied = request(window, "read-live-deny")
        respond("Deny")
        assert reply_bytes(denied) == b"", "denied read disclosed clipboard content"
        assert process.poll() is None, "frontend exited during read recovery"
        print("OK: held GDK read completes after requester close without stale consent; late consent recovers, live Allow is exact and Deny empty")
    finally:
        stop(process)
