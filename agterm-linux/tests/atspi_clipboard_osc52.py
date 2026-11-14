"""OSC 52 reaches the GDK clipboard, a denied read answers the pane once with an empty reply, and only an
advertised uri-list turns file:// text into paths."""

import base64
import contextlib
import json
import os
import subprocess
import sys
import time

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from atspi_smoke import (Atspi, activate, collect, control_json, launch, poll, stop, wait_for,
                         window_list, window_tree)

TOKEN = "agterm-osc52-token"
ENCODED = base64.b64encode(TOKEN.encode()).decode()
REPLY_QUIET_SECONDS = 2
LITERAL_URL = "file:///tmp/example"
# The second name carries shell syntax, so an unescaped paste would run `id` and split at `;`.
COPIED_FILES = "file:///tmp/one%20two\r\nfile:///tmp/%24(id)%3Bthree\r\n"
ESCAPED_PATHS = r"/tmp/one\ two /tmp/\$\(id\)\;three"
UNPROTECTED_PATHS = "/tmp/one two /tmp/$(id);three"
UTF8_TEXT = "text/plain;charset=utf-8"

# Raw mode keeps the reply away from the line discipline and the shell; a second reply lands in `replies`.
READER = rf"""
import json, os, select, sys, termios, time, tty
saved = termios.tcgetattr(0)
tty.setraw(0)
os.write(1, b"\x1b]52;c;?\x07")
deadline = time.monotonic() + 30
received = b""
while time.monotonic() < deadline:
    if select.select([0], [], [], 0.1)[0]:
        received += os.read(0, 4096)
        if b"\x1b\\" in received:
            deadline = min(deadline, time.monotonic() + {REPLY_QUIET_SECONDS})
# hand the shell back a cooked tty: a shell that does not reset modes (dash) would never see Return
termios.tcsetattr(0, termios.TCSANOW, saved)
with open(sys.argv[1] + ".tmp", "w") as result:
    json.dump(received.decode("latin-1"), result)
os.rename(sys.argv[1] + ".tmp", sys.argv[1])
"""

PASTE_READER = r"""
import os, select, sys, termios, tty
saved = termios.tcgetattr(0)
tty.setraw(0)
open(sys.argv[1] + ".ready", "w").close()
received = b""
while select.select([0], [], [], 1 if received else 30)[0]:
    received += os.read(0, 4096)
termios.tcsetattr(0, termios.TCSANOW, saved)
with open(sys.argv[1] + ".tmp", "wb") as result:
    result.write(received)
os.rename(sys.argv[1] + ".tmp", sys.argv[1])
"""

# Its own process, because an X11 selection owner must keep answering while the scenario blocks on the app.
OFFER_OWNER = r"""
import sys
import gi
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk
Gtk.init()
offers = [Gdk.ContentProvider.new_for_bytes(mime, GLib.Bytes.new(sys.argv[2].encode())) for mime in sys.argv[3:]]
Gdk.Display.get_default().get_clipboard().set_content(Gdk.ContentProvider.new_union(offers))
open(sys.argv[1], "w").close()
GLib.MainLoop().run()
"""


def osc52_reply(text):
    return f"\x1b]52;c;{base64.b64encode(text.encode()).decode()}\x1b\\"


def clipboard_text(clipboard):
    outcome = []

    def finished(source, result):
        try:
            outcome.append(source.read_text_finish(result))
        except GLib.Error:
            outcome.append(None)

    clipboard.read_text_async(None, finished)
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 3
    while not outcome and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    return outcome[0] if outcome else None


def prompt_button(app, label):
    buttons = collect(app, role="button", name=label)
    return next((button for button in buttons if button.get_state_set().contains(Atspi.StateType.SHOWING)), None)


def verify_clipboard_osc52(env):
    state = env["AGTERM_STATE_DIR"]
    os.makedirs(os.path.join(state, "config"))
    with open(os.path.join(state, "config", "ghostty.conf"), "w", encoding="utf-8") as config:
        config.write("clipboard-read = ask\nclipboard-write = allow\n")
    reader = os.path.join(state, "osc52-reader.py")
    paste_reader = os.path.join(state, "paste-reader.py")
    offer_owner = os.path.join(state, "offer-owner.py")
    for path, source in ((reader, READER), (paste_reader, PASTE_READER), (offer_owner, OFFER_OWNER)):
        with open(path, "w", encoding="utf-8") as script:
            script.write(source)

    process, app = launch(env)
    try:
        window = next(row["id"] for row in window_list(env) if row["open"])
        session = window_tree(env, window)["workspaces"][0]["sessions"][0]["id"]

        def type_line(line):
            control_json(env, "session", "type", line + "\n", "--target", session, "--window", window, "--json")

        def read_answering(label, result):
            type_line(f"{sys.executable} {reader} {result}")
            try:
                button = wait_for(lambda: prompt_button(app, label), "OSC 52 read did not prompt")
            except AssertionError:
                # the diagnostic must not mask the failure it explains
                try:
                    showing = sorted({(node.get_role_name(), node.get_name() or "") for node in collect(app, role="button")
                                      if node.get_state_set().contains(Atspi.StateType.SHOWING)})
                    pane = control_json(env, "session", "text", "--target", session, "--window", window, "--json")
                    print(f"diagnostic: showing buttons {showing}; result exists {os.path.exists(result)}; "
                          f"pane {json.dumps(pane.get('result', pane))[-1500:]}", flush=True)
                except Exception as error:
                    print(f"diagnostic unavailable: {error!r}", flush=True)
                raise
            activate(button)
            wait_for(lambda: not prompt_button(app, label), "the clipboard prompt stayed after its answer")
            assert not poll(lambda: prompt_button(app, "Deny"), REPLY_QUIET_SECONDS), (
                "one OSC 52 read prompted twice"
            )
            wait_for(lambda: os.path.exists(result), "the pane never finished its OSC 52 read", timeout=35)
            with open(result, encoding="utf-8") as reply:
                return json.load(reply)

        Gtk.init()
        clipboard = Gdk.Display.get_default().get_clipboard()
        type_line(f"printf '\\033]52;c;{ENCODED}\\007'")
        wait_for(lambda: clipboard_text(clipboard) == TOKEN, "OSC 52 write did not reach the GDK clipboard")

        denied = read_answering("Deny", os.path.join(state, "denied"))
        assert denied == "\x1b]52;c;\x1b\\", f"a denied read did not get the empty reply once: {denied!r}"

        allowed = read_answering("Allow", os.path.join(state, "allowed"))
        assert allowed == f"\x1b]52;c;{ENCODED}\x1b\\", f"an allowed read did not get the clipboard: {allowed!r}"
        print("OK: OSC 52 write reached GDK; a denied read answered once, empty, and an allowed read got the clipboard")

        results = iter(range(1000))

        def read_clipboard():
            return read_answering("Allow", os.path.join(state, f"read-{next(results)}"))

        def pasted():
            result = os.path.join(state, f"paste-{next(results)}")
            type_line(f"{sys.executable} {paste_reader} {result}")
            wait_for(lambda: os.path.exists(result + ".ready"), "the paste reader never started")
            control_json(env, "session", "paste", "--target", session, "--window", window, "--json")
            wait_for(lambda: os.path.exists(result), "the pane never finished its paste", timeout=35)
            with open(result, encoding="utf-8") as reply:
                return reply.read()

        @contextlib.contextmanager
        def offered(text, *mimes):
            ready = os.path.join(state, f"offer-{next(results)}")
            owner = subprocess.Popen([sys.executable, offer_owner, ready, text, *mimes])
            try:
                wait_for(lambda: os.path.exists(ready), "the clipboard owner never started")
                wait_for(lambda: clipboard_text(clipboard) == text, f"the clipboard never offered {mimes}")
                yield
            finally:
                owner.terminate()
                owner.wait(timeout=5)

        with offered(LITERAL_URL, UTF8_TEXT):
            literal = read_clipboard()
            assert literal == osc52_reply(LITERAL_URL), f"a read rewrote literal file:// text: {literal!r}"
            literal = pasted()
            assert literal == LITERAL_URL, f"a paste rewrote literal file:// text: {literal!r}"

        # Fork boundary, deliberately unlike upstream 1.4 (which converts for every reader, unescaped): only a
        # paste turns a file list into paths, shell-escaped like a drop; a clipboard query gets the exact bytes.
        with offered(COPIED_FILES, "text/uri-list", UTF8_TEXT):
            reply = read_clipboard()
            # negative arms first, so a dropped protection names itself
            assert reply not in (osc52_reply(UNPROTECTED_PATHS), osc52_reply(ESCAPED_PATHS)), (
                f"an OSC 52 read rewrote a file list into paths: {reply!r}")
            assert reply == osc52_reply(COPIED_FILES), f"an OSC 52 read changed the offered bytes: {reply!r}"
            paths = pasted()
            assert paths != UNPROTECTED_PATHS, f"a paste delivered file paths unescaped: {paths!r}"
            assert paths == ESCAPED_PATHS, f"a paste did not shell-escape an advertised uri-list: {paths!r}"

        type_line(f"printf '\\033]52;c;{base64.b64encode(LITERAL_URL.encode()).decode()}\\007'")
        wait_for(lambda: clipboard_text(clipboard) == LITERAL_URL, "the file:// write did not reach the GDK clipboard")
        round_trip = read_clipboard()
        assert round_trip == osc52_reply(LITERAL_URL), f"a written file:// text did not round-trip: {round_trip!r}"
        print("OK: file:// text round-trips; an advertised uri-list reads byte-exact and pastes as escaped paths")
    finally:
        stop(process)
