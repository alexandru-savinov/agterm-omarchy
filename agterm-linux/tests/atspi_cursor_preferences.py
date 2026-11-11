"""The `cursor-preferences` AT-SPI scenario: Config default versus explicit cursor shape and blink.

The cursor is read as TERMINAL STATE through an in-shell DECRQSS query for DECSCUSR (`DCS $ q SP q ST`),
never as rendered pixels. The pinned libghostty answers `DCS 1 $ r Ps SP q ST` from the active screen's
cursor style and DEC mode 12, which are exactly where `cursor-style` and `cursor-style-blink` land once the
config reaches a surface (Terminal.setDefaultCursorStyle / setDefaultCursorBlink). Ps is 1/2 for a
blinking/steady block, 3/4 for underline and 5/6 for bar.

Layers, lowest first, as GhosttyApp.buildConfig loads them: the bundled `cursor-style = block` with no
blink key (emulator default: blinking, Ps 1), the agterm-scoped `<config>/ghostty.conf`, then the UI
settings. Each layer is given a value the others never produce, so every reply names the layer that won.

The preference is written the way the Settings combo's handlers persist it (`settings.json`) and applied
through `config reload`, which calls the same `reloadConfig()` those handlers call. Driving the combo rows
themselves is NOT covered here.
"""

import itertools
import json
import os
import re
import shlex
import sys
import time

from atspi_smoke import control_json, launch, stop, wait_for, window_list, window_tree

SCOPED_CONF = "cursor-style = underline\ncursor-style-blink = false\n"
SCOPED_DEFAULT = "4"  # steady underline; the bundled layer alone would read 1 (blinking block)
EXPLICIT_BAR_BLINK, EXPLICIT_BLOCK_STEADY = "5", "2"

PROBE = r'''
import os, re, select, sys, termios, time
tag = sys.argv[1]
fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
saved = termios.tcgetattr(fd)
value = "timeout"
try:
    mode = termios.tcgetattr(fd)
    mode[3] &= ~(termios.ICANON | termios.ECHO)
    mode[6][termios.VMIN], mode[6][termios.VTIME] = 0, 0
    termios.tcsetattr(fd, termios.TCSANOW, mode)
    termios.tcflush(fd, termios.TCIFLUSH)
    os.write(fd, b"\033P$q q\033\\")
    reply, deadline = b"", time.monotonic() + 2
    pattern = re.compile(rb"\033P([01])\$r(?:(\d+) q)?\033\\")
    while time.monotonic() < deadline:
        if select.select([fd], [], [], max(0.0, deadline - time.monotonic()))[0]:
            reply += os.read(fd, 256)
            match = pattern.search(reply)
            if match:
                value = match.group(2).decode() if match.group(1) == b"1" and match.group(2) else "invalid"
                break
finally:
    termios.tcsetattr(fd, termios.TCSANOW, saved)
    os.close(fd)
print(f"CURSORPROBE-{tag}={value}")
'''


def verify_cursor_preferences(env):
    state = env["AGTERM_STATE_DIR"]
    # The scoped layer's path is derived the way the app derives it under AGTERM_STATE_DIR
    # (ConfigPaths.configDirectory → <state>/config/ghostty.conf); ensureStarterFiles never overwrites it.
    config_dir = os.path.join(state, "config")
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "ghostty.conf"), "w", encoding="utf-8") as target:
        target.write(SCOPED_CONF)
    probe_path = os.path.join(state, "cursor-probe.py")
    with open(probe_path, "w", encoding="utf-8") as target:
        target.write(PROBE)
    # The store's seeded default (SettingsStore.seededDefault) with no cursor keys: Config default for both.
    settings_path = os.path.join(state, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as target:
        json.dump({"theme": "agterm"}, target)
    probe_tags = itertools.count()

    process, _ = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])

        def sessions():
            return [item for workspace in window_tree(env, window_id)["workspaces"]
                    for item in workspace["sessions"]]

        def session(session_id):
            return next((item for item in sessions() if item["id"] == session_id), None)

        def new_session(name):
            control_json(env, "session", "new", "--name", name, "--cwd", state,
                         "--window", window_id, "--json")
            session_id = wait_for(lambda: next((item["id"] for item in sessions() if item["name"] == name), None),
                                  f"session {name!r} never appeared in the tree")
            wait_for(lambda: (session(session_id) or {}).get("foregroundShell")
                     or (session(session_id) or {}).get("foreground"),
                     f"session {name!r} never had a live shell", timeout=60)
            return session_id

        def decscusr(session_id):
            """One probe run in the session's shell; None when its marker never appeared."""
            tag = f"c{next(probe_tags)}"
            control_json(env, "session", "type",
                         f"\n{shlex.quote(sys.executable)} {shlex.quote(probe_path)} {tag}\n",
                         "--target", session_id, "--window", window_id, "--json")
            marker = re.compile(rf"CURSORPROBE-{tag}=(\S+)")

            def reported():
                text = control_json(env, "session", "text", "--lines", "40", "--target", session_id,
                                    "--window", window_id, "--json")["result"].get("text", "")
                match = marker.search(text)
                return match.group(1) if match else None

            return wait_for(reported, "", timeout=5, required=False)

        def expect(session_id, expected, what, timeout=30):
            """Poll fresh probes until DECSCUSR reads `expected`; the mutation is never repeated."""
            deadline, seen = time.monotonic() + timeout, []
            while time.monotonic() < deadline:
                value = decscusr(session_id)
                seen.append(value)
                if value == expected:
                    print(f"OK: {what}: DECSCUSR {value}")
                    return
            raise AssertionError(f"{what}: DECSCUSR reported {seen[-3:]}, expected {expected}")

        def set_preference(style, blink):
            """Persist the pair the way setCursorStyleAtIndex/setCursorBlinkAtIndex do, then reload.

            None is the combo's "Config default": the key is removed, exactly what a nil Optional encodes.
            """
            with open(settings_path, encoding="utf-8") as source:
                settings = json.load(source)
            for key, value in (("cursorStyle", style), ("cursorBlink", blink)):
                if value is None:
                    settings.pop(key, None)
                else:
                    settings[key] = value
            temporary = settings_path + ".cursor-test"
            with open(temporary, "w", encoding="utf-8") as target:
                json.dump(settings, target)
            os.replace(temporary, settings_path)
            result = control_json(env, "config", "reload", "--json")
            assert result["ok"], f"config reload failed: {result}"

        def make_inactive(session_id, other_id):
            control_json(env, "session", "select", "--target", other_id, "--window", window_id, "--json")
            wait_for(lambda: (session(other_id) or {}).get("active")
                     and not (session(session_id) or {}).get("active"),
                     f"session {other_id} never became the active session")

        existing = wait_for(lambda: sessions()[0]["id"] if sessions() else None, "no initial session")
        wait_for(lambda: (session(existing) or {}).get("foregroundShell")
                 or (session(existing) or {}).get("foreground"),
                 "the initial session never had a live shell", timeout=60)

        # Box 1: the scoped layer beats the bundled block (Ps 1) under Config default.
        expect(existing, SCOPED_DEFAULT, "Config default resolves the scoped ghostty.conf on an existing surface")

        # Box 1: an explicit UI value beats the scoped layer, on the existing surface and on a new one.
        set_preference("bar", True)
        expect(existing, EXPLICIT_BAR_BLINK, "explicit bar+blink reaches an existing surface")
        after_explicit = new_session("cursor-after-explicit")
        expect(after_explicit, EXPLICIT_BAR_BLINK, "explicit bar+blink reaches a new surface")

        # An explicit block is not Config default: it must override the scoped underline.
        set_preference("block", False)
        expect(existing, EXPLICIT_BLOCK_STEADY, "explicit block+off overrides the scoped underline")

        # Box 3: the persisted blink is terminal state on an INACTIVE surface too. Ghostty draws an
        # unfocused cursor hollow and still; that rendering is not observable here (no pixel readback).
        set_preference("bar", True)
        make_inactive(existing, after_explicit)
        expect(existing, EXPLICIT_BAR_BLINK, "persisted blink on an inactive surface")
        expect(after_explicit, EXPLICIT_BAR_BLINK, "persisted blink on the active surface")

        # Box 2: Config default again — the scoped values return on every existing and new surface.
        set_preference(None, None)
        expect(existing, SCOPED_DEFAULT, "Config default restores the scoped value on an existing surface")
        expect(after_explicit, SCOPED_DEFAULT, "Config default restores the scoped value on a later surface")
        after_default = new_session("cursor-after-default")
        expect(after_default, SCOPED_DEFAULT, "Config default resolves the scoped value on a new surface")
        print("OK: cursor shape/blink follow explicit preferences over scoped ghostty.conf and fall back")
    finally:
        stop(process)
