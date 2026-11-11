"""The `blink-unmapped` AT-SPI scenario (#166): the status-glyph pulse stops while its glyph is unmapped.

Opacity and the GLib blink source are invisible to AT-SPI, so the app is launched with the DEBUG-only
`AGTERM_ATSPI_BLINK_TRACE=1` (`BlinkPhaseTrace.swift`), which writes one stderr line per resync, per
applied phase and per stop (`event=unmap`, also written when the window is minimized). It sees a pulse
only through the phases it APPLIES: a GLib source that leaked but whose tick the coordinator drops is
invisible here, and is `BlinkPhaseSourceGLibTests`' job. Every negative assertion ("window A got no late
update") is measured against a POSITIVE CONTROL instead of a sleep: a second window B keeps one blinking
glyph mapped, its timer runs on the same main loop at the same half period, and A must apply nothing while
B applies `CLOCK_TICKS` more phases. A pulse still armed on A would apply a phase in between.

Legs: sidebar hide/show, window minimize/restore (X11 iconify under the runner's openbox, judged only once
`window list` reports the window minimized, else UNVERIFIED; native Wayland has no minimized state and is
not exercised here), window close, and a separate launch with GTK's
`gtk-enable-animations=false` for reduced motion, run as a second instance beside the first one so
window B is its clock too.
"""

import ctypes
import os
import struct
import time
import re
import tempfile

from atspi_smoke import (control_json, launch, named, select_window, stop, wait_for, window_list,
                         window_tree)

TRACE_KEY = "AGTERM_ATSPI_BLINK_TRACE"
LINE = re.compile(r"^agterm-blink-trace: window=(\S+) event=(\S+)((?: [a-z]+=\S+)*)$")
# Four applies on B span at least three half periods (>= 1.8 s on its 0.6 s timer) on one main loop.
CLOCK_TICKS = 4
TIMEOUT = 40   # bound for a slow aarch64 runner; every wait below ends on an observed event


class AnimationSettings:
    """One integer on the runner's private X screen; never replace a desktop settings owner.

    Wire format: https://specifications.freedesktop.org/xsettings/0.5/
    Created before the subject application connects, so it discovers the selection at startup.
    """

    def __init__(self):
        # Pin the XSETTINGS integer record's endian, padding, serial and Boolean representation.
        expected = bytes.fromhex(
            "00000000070000000100000000001400"
            "47746b2f456e61626c65416e696d6174696f6e73"
            "0700000000000000")
        assert self.payload(7, False) == expected
        assert self.payload(8, True)[-8:] == bytes.fromhex("0800000001000000")
        self.x = ctypes.CDLL("libX11.so.6")
        pointer, xid, integer = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
        signatures = {
            "XOpenDisplay": ([ctypes.c_char_p], pointer),
            "XDefaultScreen": ([pointer], integer),
            "XRootWindow": ([pointer, integer], xid),
            "XInternAtom": ([pointer, ctypes.c_char_p, integer], xid),
            "XGetSelectionOwner": ([pointer, xid], xid),
            "XCreateSimpleWindow": ([pointer, xid, integer, integer, ctypes.c_uint,
                                     ctypes.c_uint, ctypes.c_uint, xid, xid], xid),
            "XSetSelectionOwner": ([pointer, xid, xid, xid], integer),
            "XChangeProperty": ([pointer, xid, xid, xid, integer, integer,
                                 ctypes.c_char_p, integer], integer),
            "XSync": ([pointer, integer], integer),
            "XDestroyWindow": ([pointer, xid], integer),
            "XCloseDisplay": ([pointer], integer),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.x, name)
            function.argtypes, function.restype = arguments, result
        self.display = self.x.XOpenDisplay(None)
        assert self.display, "live reduced motion requires the runner's X11 display"
        self.window = 0
        self.serial = 0
        try:
            screen = self.x.XDefaultScreen(self.display)
            self.selection = self.x.XInternAtom(self.display, f"_XSETTINGS_S{screen}".encode(), 0)
            assert not self.x.XGetSelectionOwner(self.display, self.selection), (
                "refusing to replace an existing XSETTINGS manager")
            self.property = self.x.XInternAtom(self.display, b"_XSETTINGS_SETTINGS", 0)
            root = self.x.XRootWindow(self.display, screen)
            self.window = self.x.XCreateSimpleWindow(self.display, root, 0, 0, 1, 1, 0, 0, 0)
            assert self.window, "could not create the fixture's XSETTINGS window"
            self.x.XSetSelectionOwner(self.display, self.selection, self.window, 0)
            self.x.XSync(self.display, 0)
            self.set_enabled(True)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def payload(serial, enabled):
        name = b"Gtk/EnableAnimations"
        return (struct.pack("<B3xII", 0, serial, 1)
                + struct.pack("<BBH", 0, 0, len(name)) + name + bytes((-len(name)) % 4)
                + struct.pack("<Ii", serial, int(enabled)))

    def set_enabled(self, enabled):
        assert self.x.XGetSelectionOwner(self.display, self.selection) == self.window, (
            "fixture lost XSETTINGS ownership")
        self.serial += 1
        data = self.payload(self.serial, enabled)
        self.x.XChangeProperty(self.display, self.window, self.property, self.property,
                               8, 0, data, len(data))
        self.x.XSync(self.display, 0)

    def close(self):
        if self.display:
            # Destroy only our window. If another owner appeared, its selection stays untouched.
            if self.window:
                self.x.XDestroyWindow(self.display, self.window)
            self.x.XCloseDisplay(self.display)
            self.display = None


def verify_live_motion(env, trace, settings, windows):
    mark = len(trace.events())
    settings.set_enabled(False)
    quiet = {
        window: await_disarm(trace, mark, window, "resync", "live reduced motion", reduced="1", dimmed="0")
        for window in windows
    }
    started = time.monotonic()
    responses = 0

    def responsive_static_interval():
        nonlocal responses
        opened = {key(item["id"]) for item in window_list(env) if item["open"]}
        assert set(windows) <= opened, "a subject window disappeared during reduced motion"
        responses += 1
        for window in windows:
            assert not trace.since(quiet[window], window, "apply"), "live reduced motion still applied blink phases"
        return responses >= CLOCK_TICKS and time.monotonic() - started >= CLOCK_TICKS * 0.6

    wait_for(responsive_static_interval, "could not observe a responsive static interval", timeout=TIMEOUT)
    mark = len(trace.events())
    settings.set_enabled(True)
    for window in windows:
        wait_for(lambda: trace.first(mark, window, "resync", armed="1", reduced="0"),
                 "reenabling animations did not re-arm the mapped glyph", timeout=TIMEOUT)
        assert_ticking(trace, mark, window, "after live animations restored")
    print("blink-unmapped: live XSETTINGS disable/enable disarmed, stayed responsive/static and resumed")


class Trace:
    """The app's blink trace lines, read from the shared stderr log starting where this launch began."""

    def __init__(self, path):
        self.path = path
        self.offset = os.path.getsize(path) if os.path.exists(path) else 0

    def events(self):
        with open(self.path, encoding="utf-8", errors="replace") as source:
            source.seek(self.offset)
            lines = source.read().splitlines()
        out = []
        for line in lines:
            match = LINE.match(line)
            if match:
                fields = dict(item.split("=", 1) for item in match.group(3).split())
                out.append(dict(fields, window=match.group(1), event=match.group(2)))
        return out

    def since(self, mark, window, event=None):
        return [e for e in self.events()[mark:]
                if e["window"] == window and (event is None or e["event"] == event)]

    def first(self, mark, window, event, **want):
        for index, entry in enumerate(self.events()[mark:], start=mark):
            if entry["window"] == window and entry["event"] == event and all(
                    entry.get(key) == value for key, value in want.items()):
                return index, entry
        return None


def first_session(env, window_id):
    sessions = wait_for(lambda: window_tree(env, window_id)["workspaces"][0]["sessions"],
                        f"window {window_id} never reported a session")
    return sessions[0]["id"]


def blink(env, window_id):
    control_json(env, "session", "status", "active", "--blink", "--target", first_session(env, window_id),
                 "--json")


def assert_ticking(trace, mark, window, what):
    """A's pulse is live: two applies after `mark`, alternating, every one on a mapped marked glyph."""
    applies = wait_for(lambda: (lambda a: a if len(a) >= 2 else None)(trace.since(mark, window, "apply")),
                       f"{what}: window {window} never applied a blink phase", timeout=TIMEOUT)
    phases = [entry["phase"] for entry in applies]
    assert phases[:2] == ["1", "0"], (
        f"{what}: the re-armed pulse did not start from full opacity and alternate, phases={phases}")
    for entry in applies:
        assert entry["marked"] == "1" and entry["mapped"] == "1", (
            f"{what}: a tick wrote a glyph that was not mapped and marked: {entry}")


def assert_silent(trace, mark, quiet, clock, what):
    """No event of any kind for `quiet` while `clock` applies CLOCK_TICKS phases after `mark`."""
    wait_for(lambda: len(trace.since(mark, clock, "apply")) >= CLOCK_TICKS,
             f"{what}: the control window {clock} stopped ticking, so the silence would prove nothing",
             timeout=TIMEOUT)
    late = trace.since(mark, quiet, "apply")
    assert not late, (
        f"{what}: window {quiet} received {len(late)} blink update(s) after its pulse stopped: {late[:3]}")


def await_disarm(trace, mark, window, event, what, **want):
    found = wait_for(lambda: trace.first(mark, window, event, armed="0", **want),
                     f"{what}: window {window} never reported its pulse disarmed ({event})", timeout=TIMEOUT)
    return found[0] + 1


def open_ids(env):
    """Window ids exactly as the control socket spells them (`UUID.uuidString`, upper-case); the trace
    writes them lower-case, so only `key()` is ever compared against trace lines."""
    return [item["id"] for item in window_list(env) if item["open"]]


def key(window_id):
    return window_id.lower()


def window_open(env, window_id):
    return next((item.get("open") for item in window_list(env) if item["id"] == window_id), None)


def window_minimized(env, window_id):
    """The surface's MINIMIZED bit as `window list` reports it; None when the window is not listed."""
    return next((bool(item.get("minimized")) for item in window_list(env) if item["id"] == window_id), None)


def verify_normal(env, trace, settings):
    """Returns the still-running process and the clock window's trace key: the reduced-motion leg runs
    while this window keeps ticking, so its own negative window has the same positive control."""
    process, _app = launch(env)
    try:
        a_id = wait_for(lambda: (open_ids(env) or [None])[0], "the launched window never opened")
        b_id = control_json(env, "window", "new", "blink-clock", "--json")["result"]["id"]
        wait_for(lambda: window_open(env, b_id), "the control window never opened")
        a, b = key(a_id), key(b_id)
        blink(env, a_id)
        blink(env, b_id)
        assert_ticking(trace, 0, a, "initial pulse on A")
        assert_ticking(trace, 0, b, "initial pulse on B")
        resyncs = trace.since(0, a, "resync")
        assert resyncs and all(entry["reduced"] == "0" for entry in resyncs), (
            f"the normal launch reported reduced motion, so the reduced-motion leg's reading is unproven: "
            f"{resyncs[:3]}")

        verify_live_motion(env, trace, settings, (a, b))

        # Hide: `sidebar hide|show` act on the frontmost window, and the column's glyphs unmap
        # synchronously, so this resync must disarm A's timer.
        select_window(env, a_id)
        mark = len(trace.events())
        control_json(env, "sidebar", "hide", "--json")
        quiet = await_disarm(trace, mark, a, "resync", "sidebar hide", mapped="0", marked="1")
        assert_silent(trace, quiet, a, b, "sidebar hidden")
        mark = len(trace.events())
        control_json(env, "sidebar", "show", "--json")
        wait_for(lambda: trace.first(mark, a, "resync", armed="1", mapped="1"),
                 "sidebar show never re-armed A's pulse", timeout=TIMEOUT)
        assert_ticking(trace, mark, a, "after sidebar show")

        # Minimize: GTK keeps an iconified window's widgets mapped, so the app reads the surface's
        # MINIMIZED bit and must stop A outright. The leg is judged only once the window manager has
        # actually iconified A (`window list` reads that same bit); a desktop that never does so cannot
        # say anything about the pulse, and the leg then reports UNVERIFIED instead of passing.
        mark = len(trace.events())
        response = control_json(env, "window", "minimize", a_id, "on", "--json")
        assert response.get("ok"), f"window minimize on was refused: {response}"
        legs = "hide/show"
        if wait_for(lambda: window_minimized(env, a_id), "", timeout=10, required=False):
            quiet = await_disarm(trace, mark, a, "unmap", "window minimize")
            assert_silent(trace, quiet, a, b, "window minimized")
            mark = len(trace.events())
            control_json(env, "window", "minimize", a_id, "off", "--json")
            wait_for(lambda: window_minimized(env, a_id) is False, "window A never reported restored",
                     timeout=TIMEOUT)
            wait_for(lambda: trace.first(mark, a, "resync", armed="1", mapped="1"),
                     "restoring the minimized window never re-armed A's pulse", timeout=TIMEOUT)
            assert_ticking(trace, mark, a, "after window restore")
            legs += ", minimize/restore"
        else:
            print("UNVERIFIED: blink-unmapped minimize/restore: the window manager never reported "
                  f"window {a_id} minimized, so the pulse under iconify was not observed")
            control_json(env, "window", "minimize", a_id, "off", "--json")

        # Close: the mark is taken BEFORE the request, so nothing A does during teardown escapes the
        # check. Teardown may stop and normalize (unmap, a phase-0 apply, a disarmed resync); a phase-1
        # apply or an armed resync from there on is a tick reaching a dying tree. Once A reports closed,
        # it must stay completely silent while B ticks.
        mark = len(trace.events())
        control_json(env, "window", "close", a_id, "--json")
        wait_for(lambda: window_open(env, a_id) is False, "window A never reported closed")
        closed = len(trace.events())
        assert_silent(trace, closed, a, b, "window closed")
        ticks = [entry for entry in trace.since(mark, a)
                 if (entry["event"] == "apply" and entry["phase"] == "1")
                 or (entry["event"] == "resync" and entry["armed"] == "1")]
        assert not ticks, f"window close: window {a} ticked or re-armed during teardown: {ticks[:3]}"
        late = trace.since(closed, a)
        assert not late, f"window closed: window {a} still traced after close: {late[:3]}"
        print(f"blink-unmapped: {legs} and close verified on {a} against clock {b}")
        return process, b
    except BaseException:
        stop(process)
        raise


def verify_reduced_motion(env, root, trace, clock):
    config = os.path.join(root, "reduced-motion-config")
    state = os.path.join(root, "reduced-motion-state")
    os.makedirs(os.path.join(config, "gtk-4.0"))
    os.makedirs(state)
    with open(os.path.join(config, "gtk-4.0", "settings.ini"), "w", encoding="utf-8") as settings:
        settings.write("[Settings]\ngtk-enable-animations=false\n")
    # A second instance beside the clock's: its own app id (else GApplication forwards the launch to
    # the running one), state directory and socket.
    env = dict(env, XDG_CONFIG_HOME=config, AGTERM_STATE_DIR=state,
               AGTERM_CONTROL_SOCKET=os.path.join(state, "agterm.sock"),
               AGTERM_APP_ID=env["AGTERM_APP_ID"] + "_reduced")
    start = len(trace.events())
    process, app = launch(env)
    try:
        window_id = wait_for(lambda: (open_ids(env) or [None])[0], "the reduced-motion window never opened")
        window = key(window_id)
        assert window != clock, "the reduced-motion launch reused the clock's window"
        blink(env, window_id)
        _, entry = wait_for(lambda: trace.first(start, window, "resync", marked="1", mapped="1"),
                            "the blinking glyph never reached a resync under reduced motion",
                            timeout=TIMEOUT)
        assert entry["reduced"] == "1", f"settings.ini did not reach GTK: the app read {entry}"
        assert entry["armed"] == "0" and entry["dimmed"] == "0", (
            f"reduced motion left the pulse armed or the glyph dimmed: {entry}")
        wait_for(lambda: named(app, "●", role="label"), "the status glyph is not presented under reduced motion")
        # A second resync with the glyph mapped again (hide, then show) must still decline to arm.
        mark = len(trace.events())
        control_json(env, "sidebar", "hide", "--json")
        control_json(env, "sidebar", "show", "--json")
        found = wait_for(lambda: trace.first(mark, window, "resync", mapped="1"),
                         "sidebar show never resynced the reduced-motion window", timeout=TIMEOUT)
        again = found[1]
        assert again["armed"] == "0" and again["dimmed"] == "0", f"reduced motion re-armed on show: {again}"
        # The static glyph must stay static while the normal instance's clock keeps pulsing.
        assert_silent(trace, found[0] + 1, window, clock, "reduced motion")
        ticks = [e for e in trace.since(start, window, "apply") if e["phase"] == "1"]
        assert not ticks, f"reduced motion still dimmed the glyph on a tick: {ticks[:3]}"
        print(f"blink-unmapped: reduced motion keeps {window}'s glyph static at full opacity")
    finally:
        stop(process)


def verify_blink_unmapped(env):
    path = os.environ.get("AGTERM_UI_APP_STDERR")
    assert path, "AGTERM_UI_APP_STDERR is unset: the blink trace would go to /dev/null and prove nothing"
    env = dict(env, **{TRACE_KEY: "1"})
    trace = Trace(path)
    settings = AnimationSettings()
    try:
        process, clock = verify_normal(env, trace, settings)
    finally:
        settings.close()
    try:
        with tempfile.TemporaryDirectory(prefix="agterm-blink-") as root:
            verify_reduced_motion(env, root, trace, clock)
    finally:
        stop(process)
