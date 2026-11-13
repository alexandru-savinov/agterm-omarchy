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
Headerbar pixels must match the active theme in existing and new windows. Preferences edits must
change the active or alternate persisted slot without changing the other slot, on both desktop sides.
"""

from collections import Counter
from pathlib import Path

import itertools
import os
import re
import select
import shlex
import subprocess
import sys
import time

from atspi_smoke import (activate, collect, control_json, launch, mouse_click, press_x11_key, stop,
                         type_x11_text, wait_for, window_list, window_tree)
from atspi_prefs_combo import (close_preferences, label_counts, open_preferences,
                              preferences_row, read_settings)
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
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self.pending = b""
        self.expect_line("READY", "the appearance portal fixture never owned its bus name")

    def expect_line(self, wanted, message, timeout=10):
        """The fixture's next line, read in raw chunks so a partial line cannot block past the deadline."""
        deadline, stdout = time.monotonic() + timeout, self.process.stdout.fileno()
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stdout], [], [], remaining)[0]:
                raise AssertionError(f"{message}: no full line within {timeout}s "
                                     f"(partial {self.pending!r}, exit status {self.process.poll()})")
            chunk = os.read(stdout, 4096)
            if not chunk:
                raise AssertionError(f"{message}: fixture closed its output (exit status {self.process.poll()})")
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        if line.decode(errors="replace").strip() != wanted:
            raise AssertionError(f"{message}: fixture said {line!r}")

    def flip(self, side):
        self.process.stdin.write(side.encode() + b"\n")
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
        process, app = launch(env)
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

        titles = {}
        artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", state)) / "appearance-chrome"
        artifacts.mkdir(parents=True, exist_ok=True)

        def chrome(window_id):
            # Reuse the theme-cancellation scenario's exposed header strip, not terminal pixels.
            if window_id not in titles:
                visible = next(item["id"] for item in sessions(window_id) if item.get("active"))
                titles[window_id] = f"appearance-chrome-{len(titles)}"
                result = control_json(env, "session", "rename", titles[window_id], "--target", visible,
                                      "--window", window_id, "--json")
                assert result["ok"], result
            frame = wait_for(lambda: next((item for item in collect(app, role="frame")
                                           if (item.get_name() or "").startswith(titles[window_id])), None),
                             f"window {titles[window_id]} never appeared")
            matches = subprocess.check_output(
                ["xdotool", "search", "--onlyvisible", "--all", "--pid", str(process.pid),
                 "--name", f"^{re.escape(frame.get_name())}$"], text=True, timeout=5).splitlines()
            assert len(matches) == 1, f"ambiguous chrome window: {matches}"
            xid = matches[0]
            assert subprocess.check_output(["xdotool", "getwindowpid", xid], text=True).strip() == str(process.pid)
            subprocess.run(["xdotool", "windowraise", xid], check=True, timeout=5)
            image = artifacts / f"{titles[window_id]}-{next(tags)}.png"
            subprocess.run(["import", "-window", xid, str(image)], check=True, timeout=10)
            width = int(subprocess.check_output(["convert", str(image), "-format", "%w", "info:"], text=True))
            assert width > 200
            pixels = subprocess.check_output(
                ["convert", str(image), "-crop", f"{width - 80}x8+40+5", "+repage",
                 "-alpha", "off", "-depth", "8", "rgb:-"], timeout=10)
            color, count = Counter(zip(pixels[::3], pixels[1::3], pixels[2::3])).most_common(1)[0]
            assert count > len(pixels) // 6, "headerbar strip has no dominant painted background"
            return "#" + "".join(f"{value:02x}" for value in color)

        def check_chrome(window_id, side):
            expected = THEMES[THEME_FOR_SIDE[side]][0]
            wait_for(lambda: chrome(window_id) == expected,
                     f"window {window_id} chrome did not become {expected} on the {side} desktop")
            print(f"OK: window {window_id} rendered headerbar is {expected} on the {side} desktop")

        def preferences_fields(side):
            active, alternate = ("theme", "darkTheme") if side == "light" else ("darkTheme", "theme")
            original = {"theme": LIGHT, "darkTheme": DARK}
            open_preferences(app, process.pid, "Appearance")
            for title, field, other in (("Theme", active, alternate),
                                        ("Dark theme" if side == "light" else "Light theme", alternate, active)):
                # Change away and back so an already-selected row cannot satisfy the persistence check.
                for target in (original[other], original[field]):
                    before = read_settings(env)
                    labels = label_counts(app)
                    row = wait_for(lambda: preferences_row(app, title), f"missing {title} row")
                    try:
                        has_action = bool(row.get_action_iface() and row.get_action_iface().get_n_actions() > 0)
                    except Exception:
                        has_action = False
                    if has_action:
                        activate(row)
                    else:
                        mouse_click(lambda: preferences_row(app, title), process.pid, button="left")
                    wait_for(lambda: label_counts(app) != labels, f"{title} list did not open")
                    # Long theme lists expose only visible rows; search before looking for the target.
                    type_x11_text(target, process.pid)
                    wait_for(lambda: label_counts(app).get(target, 0) > labels.get(target, 0),
                             f"{title} search did not expose {target}")
                    press_x11_key("Down", process.pid)
                    press_x11_key("Return", process.pid)
                    wait_for(lambda: read_settings(env).get(field) == target,
                             f"{title} selection did not persist {target} in {field}")
                    saved = read_settings(env)
                    assert saved.get(other) == before[other], (
                        f"{side} {title} changed the other persisted slot: {before} -> {saved}")
                    assert saved.get("followSystemAppearance") is True, saved
            close_preferences(app, process.pid)
            saved = read_settings(env)
            assert (saved.get("theme"), saved.get("darkTheme")) == (LIGHT, DARK), saved
            print(f"OK: {side} Preferences active/alternate edits preserve the opposite persisted slot")

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

        preferences_fields("light")
        for window_id in (first_window, second_window):
            check_chrome(window_id, "light")
        palettes = {"light": check_all("light", "on the light desktop before any flip")[3]}

        for side in ("dark", "light"):
            portal.flip(side)
            phase = f"after the desktop flipped to {side}"
            expect_reported_theme(THEME_FOR_SIDE[side], phase)
            existing = check_all(side, phase)
            for existing_window in dict.fromkeys(item[1] for item in targets):
                check_chrome(existing_window, side)
            palettes[side] = existing[3]
            label = f"window created {phase}"
            window_id, session_id = new_window(f"appearance-after-{side}")
            created = look(window_id, session_id, side, label)
            check_chrome(window_id, side)
            assert created == existing, f"{label} reports {created}, existing windows report {existing}"
            print(f"OK: the {label} matches the existing windows: {created}")
            targets.append((label, window_id, session_id))
            # Only edit Preferences AFTER all portal-driven surface/chrome checks, so a settings
            # reload cannot repair a missing appearance-notification update and hide a regression.
            preferences_fields(side)
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
