"""The `appearance-follow` AT-SPI scenario: a SYSTEM light/dark flip across existing windows (#158).

The desktop's part is played by `atspi_appearance_portal.py` on the runner's private session bus; the app
is unmodified and learns of each flip the way it does on a real desktop, through libadwaita's
`notify::dark`. Two windows each hold a visible and a hidden (unselected) session. After each flip, in
both directions, every surface is read back from INSIDE its shell:

- OSC 10/11 foreground/background against the theme's known colors,
- OSC 4;1 (palette entry 1), which must agree across surfaces and differ between the two sides,
- `CSI ? 996 n`, the color-scheme report (`CSI ? 997 ; 1 n` dark, `; 2 n` light) that Ghostty answers.

A window created after each flip must report exactly what the existing windows report. A final arm turns
follow-system off and flips again: the reported scheme must still follow the desktop while the palette
stays on the single theme, so neither check can pass by tracking the other.
Window chrome (a display-wide CSS provider) and the Preferences controls are not observed here.
"""

import itertools
import os
import re
import select
import shlex
import subprocess
import sys
import time

from atspi_smoke import control_json, launch, stop, wait_for, window_list, window_tree
from atspi_theme_many_sessions import THEMES

LIGHT, DARK = "Atom One Light", "Dracula"
THEME_FOR_SIDE = {"light": LIGHT, "dark": DARK}
PORTAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atspi_appearance_portal.py")

# `scheme` writes the DSR color-scheme query; any other query is an OSC color query such as `10` or `4;1`.
PROBE = r'''
import os, re, select, sys, termios, time
query, tag = sys.argv[1], sys.argv[2]
if query == "scheme":
    request, pattern = b"\033[?996n", re.compile(rb"\033\[\?997;([12])n")
else:
    request = f"\033]{query};?\033\\".encode()
    pattern = re.compile(rb"\033\]" + re.escape(query.encode()) + rb";rgb:([0-9a-fA-F/]+)(?:\007|\033\\)")
fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
saved = termios.tcgetattr(fd)
value = "timeout"
try:
    mode = termios.tcgetattr(fd)
    mode[3] &= ~(termios.ICANON | termios.ECHO)
    mode[6][termios.VMIN], mode[6][termios.VTIME] = 0, 0
    termios.tcsetattr(fd, termios.TCSANOW, mode)
    termios.tcflush(fd, termios.TCIFLUSH)
    os.write(fd, request)
    reply, deadline = b"", time.monotonic() + 2
    while time.monotonic() < deadline:
        if select.select([fd], [], [], max(0.0, deadline - time.monotonic()))[0]:
            reply += os.read(fd, 256)
            match = pattern.search(reply)
            if match:
                if query == "scheme":
                    value = "dark" if match.group(1) == b"1" else "light"
                else:
                    channels = match.group(1).decode().split("/")
                    value = "#" + "".join(
                        "%02x" % round(int(c, 16) * 255 / (16 ** len(c) - 1)) for c in channels)
                break
finally:
    termios.tcsetattr(fd, termios.TCSANOW, saved)
    os.close(fd)
print(f"APPEARANCEPROBE-{tag}={value}")
'''


class Portal:
    """The fake desktop appearance service, owned by this scenario for the app's whole life."""

    def __init__(self, initial):
        self.process = subprocess.Popen(
            [sys.executable, "-I", PORTAL, initial],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.expect_line("READY", "the appearance portal fixture never owned its bus name")

    def expect_line(self, wanted, message, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([self.process.stdout], [], [], max(0.0, deadline - time.monotonic()))[0]:
                line = self.process.stdout.readline()
                if not line:
                    break
                if line.strip() == wanted:
                    return
                raise AssertionError(f"{message}: fixture said {line.strip()!r}")
        raise AssertionError(f"{message} (exit status {self.process.poll()})")

    def flip(self, side):
        self.process.stdin.write(side + "\n")
        self.process.stdin.flush()
        self.expect_line(f"SET {side}", f"the portal fixture did not publish {side}")

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def verify_appearance_follow(env):
    state = env["AGTERM_STATE_DIR"]
    probe_path = os.path.join(state, "appearance-probe.py")
    with open(probe_path, "w", encoding="utf-8") as target:
        target.write(PROBE)
    tags = itertools.count()

    portal = Portal("light")
    process = None
    try:
        process, _ = launch(env)
        first_window = next(item["id"] for item in window_list(env) if item["open"])

        def sessions(window_id):
            return [item for workspace in window_tree(env, window_id)["workspaces"]
                    for item in workspace["sessions"]]

        def session_node(window_id, session_id):
            return next((item for item in sessions(window_id) if item["id"] == session_id), None)

        def wait_live(window_id, session_id):
            def live():
                node = session_node(window_id, session_id)
                return node and node.get("fontSize") and (node.get("foregroundShell") or node.get("foreground"))
            wait_for(live, f"session {session_id} in window {window_id} never had a live shell", timeout=60)

        def new_window(name):
            window_id = control_json(env, "window", "new", name, "--json")["result"]["id"]
            session_id = wait_for(lambda: next((item["id"] for item in sessions(window_id)), None),
                                  f"window {name!r} never showed a session")
            wait_live(window_id, session_id)
            return window_id, session_id

        def hidden_session(window_id, name, visible_id):
            control_json(env, "session", "new", "--name", name, "--cwd", state,
                         "--window", window_id, "--json")
            hidden_id = wait_for(
                lambda: next((item["id"] for item in sessions(window_id) if item["name"] == name), None),
                f"session {name!r} never appeared in the tree")
            wait_live(window_id, hidden_id)
            control_json(env, "session", "select", "--target", visible_id, "--window", window_id, "--json")
            wait_for(lambda: (session_node(window_id, visible_id) or {}).get("active"),
                     f"session {visible_id} never became the visible session again")
            return hidden_id

        def assert_hidden(window_id, session_id, what):
            node = session_node(window_id, session_id)
            assert node is not None and not node.get("active"), (
                f"{what}: the hidden session {session_id} is {node!r}, not an unselected session")

        def report(window_id, session_id, query):
            tag = f"p{next(tags)}"
            control_json(env, "session", "type",
                         f"\n{shlex.quote(sys.executable)} {shlex.quote(probe_path)} {shlex.quote(query)} {tag}\n",
                         "--target", session_id, "--window", window_id, "--json")
            marker = re.compile(rf"APPEARANCEPROBE-{tag}=(\S+)")

            def reported():
                text = control_json(env, "session", "text", "--lines", "40", "--target", session_id,
                                    "--window", window_id, "--json")["result"].get("text", "")
                match = marker.search(text)
                return match.group(1) if match else None
            return wait_for(reported, "", timeout=5, required=False)

        def expect(window_id, session_id, query, expected, what, timeout=30):
            """Poll fresh probes until `query` reports `expected`; the flip itself is never repeated."""
            deadline, seen = time.monotonic() + timeout, []
            while time.monotonic() < deadline:
                value = report(window_id, session_id, query)
                seen.append(value)
                if value == expected:
                    return value
            raise AssertionError(f"{what}: {query} reported {seen[-3:]}, expected {expected}")

        def settled(window_id, session_id, query, what):
            """Two consecutive answered probes that agree; a missed marker is retried, a change is not."""
            values, seen = [], []
            for _ in range(6):
                value = report(window_id, session_id, query)
                seen.append(value)
                if value and value != "timeout":
                    values.append(value)
                if len(values) == 2:
                    break
            assert len(values) == 2 and values[0] == values[1], f"{what}: {query} never settled ({seen})"
            return values[0]

        def look(window_id, session_id, side, what):
            """The surface's settled appearance: (scheme, foreground, background, palette entry 1)."""
            background, foreground = THEMES[THEME_FOR_SIDE[side]]
            expect(window_id, session_id, "scheme", side, f"{what} color-scheme report")
            expect(window_id, session_id, "10", foreground, f"{what} foreground")
            expect(window_id, session_id, "11", background, f"{what} background")
            return side, foreground, background, settled(window_id, session_id, "4;1", what)

        def expect_reported_theme(theme, what):
            wait_for(lambda: control_json(env, "theme", "list", "--json")["result"].get("theme") == theme,
                     f"{what}: `theme list` never reported {theme!r} as the active theme", timeout=30)

        result = control_json(env, "theme", "set", "--light", LIGHT, "--dark", DARK, "--json")
        assert result["ok"] and result["result"].get("sync") is True, f"theme set did not enable sync: {result}"
        assert (result["result"].get("light"), result["result"].get("dark")) == (LIGHT, DARK), result
        expect_reported_theme(LIGHT, "the light desktop at launch")

        first_visible = sessions(first_window)[0]["id"]
        wait_live(first_window, first_visible)
        second_window, second_visible = new_window("appearance-second")
        targets = [
            ("first window, visible", first_window, first_visible),
            ("first window, hidden", first_window,
             hidden_session(first_window, "appearance-first-hidden", first_visible)),
            ("second window, visible", second_window, second_visible),
            ("second window, hidden", second_window,
             hidden_session(second_window, "appearance-second-hidden", second_visible)),
        ]
        hidden = [(label, window_id, session_id) for label, window_id, session_id in targets if "hidden" in label]

        def check_all(side, phase):
            looks = {}
            for label, window_id, session_id in targets:
                looks[label] = look(window_id, session_id, side, f"{label} {phase}")
            distinct = set(looks.values())
            assert len(distinct) == 1, f"{phase}: surfaces disagree: {looks}"
            for label, window_id, session_id in hidden:
                assert_hidden(window_id, session_id, f"{label} {phase}")
            print(f"OK: {len(targets)} surfaces {phase} report {distinct.pop()}")
            return next(iter(looks.values()))

        palettes = {"light": check_all("light", "on the light desktop before any flip")[3]}

        for side in ("dark", "light"):
            portal.flip(side)
            phase = f"after the desktop flipped to {side}"
            expect_reported_theme(THEME_FOR_SIDE[side], phase)
            existing = check_all(side, phase)
            palettes[side] = existing[3]
            label = f"window created {phase}"
            window_id, session_id = new_window(f"appearance-after-{side}")
            created = look(window_id, session_id, side, label)
            assert created == existing, f"{label} reports {created}, existing windows report {existing}"
            print(f"OK: the {label} matches the existing windows: {created}")
            targets.append((label, window_id, session_id))
        assert palettes["light"] != palettes["dark"], (
            f"OSC 4;1 is {palettes['light']} on both sides; the palette did not follow the theme")
        print(f"OK: palette entry 1 is {palettes['light']} light and {palettes['dark']} dark")

        result = control_json(env, "theme", "set", LIGHT, "--dark", "none", "--json")
        assert result["ok"] and not result["result"].get("sync"), f"theme set did not turn sync off: {result}"
        portal.flip("dark")
        background, foreground = THEMES[LIGHT]
        for label, window_id, session_id in (targets[0], targets[1]):
            what = f"{label} with follow-system off after a flip to dark"
            expect(window_id, session_id, "scheme", "dark", f"{what} color-scheme report")
            for query, expected in (("10", foreground), ("11", background)):
                value = settled(window_id, session_id, query, what)
                assert value == expected, f"{what}: OSC {query} reported {value}, expected {expected}"
        print("OK: with follow-system off the scheme report follows the desktop and the palette does not")
    finally:
        if process is not None:
            stop(process)
        portal.close()
