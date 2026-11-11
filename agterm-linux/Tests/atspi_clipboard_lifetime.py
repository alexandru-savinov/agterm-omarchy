"""Real OSC 52 consent dialogs across session and window teardown on the isolated display."""

import base64
import json
from pathlib import Path
import time

from gi.repository import Gdk, GLib, GObject, Gtk

from atspi_smoke import (
    NEGATIVE_SETTLE_SECONDS,
    activate,
    control_json,
    launch,
    named,
    stop,
    wait_for,
    window_list,
    window_sessions,
)


HEADING = "Allow a program to set the clipboard?"


def read_clipboard(clipboard):
    answers = []

    def completed(source, result):
        answers.append(source.read_text_finish(result))

    clipboard.read_text_async(None, completed)
    deadline = time.monotonic() + 5
    context = GLib.MainContext.default()
    while not answers and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)
    assert answers, "clipboard read timed out"
    return answers[0]


def verify_clipboard_lifetime(env):
    state = Path(env["AGTERM_STATE_DIR"])
    (state / "settings.json").write_text(json.dumps({"restoreMode": "none", "closeGraceUndoEnabled": False}))
    (state / "config").mkdir(exist_ok=True)
    (state / "config/ghostty.conf").write_text("clipboard-write = ask\n")
    Gtk.init()
    clipboard = Gdk.Display.get_default().get_clipboard()
    sentinel = "agterm clipboard lifetime sentinel"
    value = GObject.Value(GObject.TYPE_STRING)
    value.set_string(sentinel)
    clipboard.set(value)
    assert read_clipboard(clipboard) == sentinel
    process, app = launch(env)

    def request(session, window, text):
        encoded = base64.b64encode(text.encode()).decode()
        response = control_json(env, "session", "type", f"printf '\\033]52;c;{encoded}\\007'\n",
                                "--target", session, "--window", window, "--json")
        assert response["ok"], response
        wait_for(lambda: named(app, HEADING), "OSC 52 write did not present consent")
        wait_for(lambda: named(app, "Allow", role="button"), "consent has no Allow button")

    def respond(response):
        activate(wait_for(lambda: named(app, response, role="button"), "consent response button missing"))
        wait_for(lambda: named(app, HEADING) is None, "consent dialog remained after response")

    def unchanged():
        deadline = time.monotonic() + NEGATIVE_SETTLE_SECONDS
        while time.monotonic() < deadline:
            assert process.poll() is None, "frontend exited during clipboard consent"
            assert read_clipboard(clipboard) == sentinel, "denied or torn-down requester changed the clipboard"
            time.sleep(0.1)

    def create_session(window, name):
        response = control_json(env, "session", "new", "--name", name, "--window", window, "--json")
        assert response["ok"], response
        return response["result"]["id"]

    try:
        window = wait_for(lambda: next((item["id"] for item in window_list(env) if item.get("active")), None),
                          "initial window did not become active")
        session = create_session(window, "clipboard-requester")
        request(session, window, "must not survive session close")
        response = control_json(env, "session", "close", "--target", session, "--window", window, "--json")
        assert response["ok"], response
        wait_for(lambda: all(item["id"] != session for item in window_sessions(env, window)),
                 "clipboard requester session did not close")
        if named(app, HEADING) is not None:
            respond("Allow")
        unchanged()

        owner = control_json(env, "window", "new", "clipboard-owner", "--json")["result"]["id"]
        session = create_session(owner, "clipboard-owner-requester")
        request(session, owner, "must not survive window close")
        response = control_json(env, "window", "close", owner, "--json")
        assert response["ok"], response
        wait_for(lambda: not any(item["id"] == owner and item.get("open") for item in window_list(env)),
                 "clipboard owner window did not close")
        wait_for(lambda: named(app, HEADING) is None, "consent dialog outlived its owner window")
        unchanged()

        session = create_session(window, "clipboard-after-disposal")
        request(session, window, "denied after disposal")
        respond("Deny")
        unchanged()
        expected = "allowed after dialog disposal"
        request(session, window, expected)
        respond("Allow")
        wait_for(lambda: read_clipboard(clipboard) == expected, "live consent did not update the clipboard")
        assert process.poll() is None, "frontend exited after disposal recovery"
        print("OK: real clipboard consent survives requester teardown, owner-window disposal and later deny/allow")
    finally:
        stop(process)
