"""Exercise Omarchy's terminal key bindings and the real notification payload under Xvfb."""

from contextlib import contextmanager
import json
from pathlib import Path
import shlex
import sys

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from atspi_clipboard_osc52 import clipboard_text
from atspi_clipboard_payloads import COLLECTOR
from atspi_smoke import control_json, launch, press_x11_key, raw_control_json, stop, wait_for, window_list, window_tree

SHIFT_ENTER = b"\x1b[13;2u"
ALT_SHIFT_ENTER = b"\x1b[13;4u"
LEGACY_SHIFT_ENTER = b"\x1b[27;2;13~"
LEGACY_ALT_SHIFT_ENTER = b"\x1b[27;4;13~"
COPIED = "omarchy-copy-sentinel"
PASTED = "omarchy-paste-sentinel"
NOTICE = "Omarchy notification name"
BUS_NAME = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
METHODS = f"""
<node><interface name="{BUS_NAME}">
  <method name="Notify">
    <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
    <arg type="a{{sv}}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/>
  </method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
</interface></node>
"""


def service_events():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@contextmanager
def notifications():
    """Record the payload received by a notification daemon on the harness's private bus."""
    received = []
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def called(_connection, _sender, _path, _interface, method, parameters, invocation):
        if method == "Notify":
            received.append(parameters.unpack())
            invocation.return_value(GLib.Variant("(u)", (1,)))
        else:
            invocation.return_value(None)

    node = Gio.DBusNodeInfo.new_for_xml(METHODS)
    registration = bus.register_object(OBJECT_PATH, node.interfaces[0], called, None, None)
    daemon = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")
    owner = bus.call_sync(*daemon, "RequestName", GLib.Variant("(su)", (BUS_NAME, 4)),
                          GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
    assert owner.unpack() == (1,), "the private notification bus name already has an owner"
    try:
        yield received
    finally:
        bus.unregister_object(registration)
        bus.call_sync(*daemon, "ReleaseName", GLib.Variant("(s)", (BUS_NAME,)),
                      GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)


def verify_omarchy_keys(env):
    Gtk.init()
    clipboard = Gdk.Display.get_default().get_clipboard()
    failures = []

    def check(name, actual, expected):
        if actual == expected:
            print(f"OK: {name} = {actual!r}", flush=True)
        else:
            message = f"{name}: got {actual!r}, expected {expected!r}"
            print(f"FAIL: {message}", flush=True)
            failures.append(message)

    profiles = (
        ("follow", SHIFT_ENTER, ALT_SHIFT_ENTER),
        ("explicit-theme-and-override", b"\x1b[99~", ALT_SHIFT_ENTER),
        ("switch-off", LEGACY_SHIFT_ENTER, LEGACY_ALT_SHIFT_ENTER),
        ("without-omarchy", LEGACY_SHIFT_ENTER, LEGACY_ALT_SHIFT_ENTER),
    )
    for index, (profile, expected_shift, expected_alt) in enumerate(profiles):
        # Keep the private UNIX socket path short even on a runner with a deep temp directory.
        root = Path(env["AGTERM_STATE_DIR"]) / str(index)
        home = root / "home"
        home.mkdir(parents=True)
        if profile != "without-omarchy":
            theme = home / ".local/state/omarchy/current/theme"
            theme.mkdir(parents=True)
            (theme / "ghostty.conf").write_text("background = #315286\n", encoding="utf-8")
        state = root / "state"
        state.mkdir()
        settings = {"restoreMode": "none"}
        if profile == "explicit-theme-and-override":
            settings["theme"] = "Dracula"
            scoped = state / "config" / "ghostty.conf"
            scoped.parent.mkdir()
            scoped.write_text("keybind = shift+enter=csi:99~\n", encoding="utf-8")
        if profile == "switch-off":
            (state / "omarchy-theme.json").write_text('{"follow":false}\n', encoding="utf-8")
        (state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        local = dict(env, HOME=str(home), AGTERM_STATE_DIR=str(state),
                     AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"), GNOTIFICATION_BACKEND="freedesktop")
        with notifications() as received:
            process, _ = launch(local)
            try:
                wait_for(Path(local["AGTERM_CONTROL_SOCKET"]).is_socket, f"{profile}: control socket did not start")
                window = wait_for(lambda: next((row["id"] for row in window_list(local) if row["open"]), None),
                                  f"{profile}: no window opened")
                session = window_tree(local, window)["workspaces"][0]["sessions"][0]["id"]

                def session_ctl(*arguments):
                    response = control_json(local, "session", *arguments, "--target", session, "--window", window, "--json")
                    assert response["ok"], response
                    return response

                reader, ready, output = root / "reader.py", root / "ready", root / "bytes"
                reader.write_text(f"print({COPIED!r}, flush=True)\n" + COLLECTOR, encoding="utf-8")
                cmd = shlex.join([sys.executable, str(reader), str(ready), str(output), "false"])
                session_ctl("type", cmd + "\n")
                wait_for(ready.exists, f"{profile}: the terminal input reader did not start")

                def compare_key(key, expected):
                    offset = len(output.read_bytes())
                    press_x11_key(key, process.pid)

                    def matches():
                        service_events()
                        return output.read_bytes()[offset:] == expected

                    wait_for(matches, "", timeout=2, required=False)
                    check(f"{profile} {key} PTY bytes", output.read_bytes()[offset:], expected)

                compare_key("shift+Return", expected_shift)
                compare_key("alt+shift+Return", expected_alt)
                if profile != "follow":
                    continue

                offer = GObject.Value(GObject.TYPE_STRING)
                offer.set_string(PASTED)
                clipboard.set(offer)
                compare_key("shift+Insert", PASTED.encode())

                session_ctl("select-all")
                press_x11_key("ctrl+Insert", process.pid)
                copied = wait_for(lambda: COPIED in (clipboard_text(clipboard) or ""),
                                  "", timeout=3, required=False)
                check("Ctrl+Insert copies the terminal selection", bool(copied), True)

                response = raw_control_json(local, {"cmd": "notify", "target": session,
                                                    "args": {"window": window, "title": NOTICE, "body": "key parity"}})
                assert response["ok"], response

                def notice():
                    service_events()
                    return next((entry for entry in received if entry[3] == NOTICE), None)

                payload = wait_for(notice, "the private notification service never received Notify")
                check("notification display name", payload[0], "agterm")
                check("notification desktop entry", payload[6].get("desktop-entry"), local["AGTERM_APP_ID"])
            finally:
                stop(process)
    assert not failures, "\n".join(failures)
