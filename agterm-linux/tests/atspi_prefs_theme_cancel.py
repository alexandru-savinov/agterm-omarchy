"""Every way out of the theme picker except Enter must leave the committed theme on every terminal.

Escape, a window-manager close and a parent-window close each land WITHIN the 70 ms preview debounce: the
arrow presses and the cancel go out in one xdotool call, so a preview scheduled by the last arrow is still
pending when the cancel runs. Colors are read back through each shell's OSC 10/11 replies.
"""

import itertools
import os
import re
import shlex
import subprocess
import sys
import time

from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, collect, control_json, focus_accessible_window, launch,
                         named, run_palette_action, stop, wait_for, window_list, window_tree)
from atspi_prefs_combo import read_settings, seed_settings
from atspi_theme_many_sessions import PROBE, THEMES

COMMITTED = "Dracula"


def verify_theme_picker_cancel(env):
    state = env["AGTERM_STATE_DIR"]
    probe_path = os.path.join(state, "color-probe.py")
    with open(probe_path, "w", encoding="utf-8") as target:
        target.write(PROBE)
    tags = itertools.count()
    seed_settings(env, theme=COMMITTED)
    process, app = launch(env)
    try:
        first_window = next(item["id"] for item in window_list(env) if item["open"])

        def osc(window_id, session_id, query):
            tag = f"t{next(tags)}"
            control_json(env, "session", "type",
                         f"\n{shlex.quote(sys.executable)} {shlex.quote(probe_path)} {query} {tag}\n",
                         "--target", session_id, "--window", window_id, "--json")
            marker = re.compile(rf"COLORPROBE-{tag}=(\S+)")

            def reported():
                text = control_json(env, "session", "text", "--lines", "40", "--target", session_id,
                                    "--window", window_id, "--json")["result"].get("text", "")
                match = marker.search(text)
                return match.group(1) if match else None
            return wait_for(reported, "", timeout=5, required=False)

        def open_sessions():
            return [(window["id"], session["id"]) for window in window_list(env) if window["open"]
                    for workspace in window_tree(env, window["id"])["workspaces"]
                    for session in workspace["sessions"]]

        def assert_committed(what):
            time.sleep(NEGATIVE_SETTLE_SECONDS)
            assert read_settings(env).get("theme") == COMMITTED, (
                f"{what} rewrote the saved theme to {read_settings(env).get('theme')!r}")
            background, foreground = THEMES[COMMITTED]
            for window_id, session_id in open_sessions():
                for query, expected in ((11, background), (10, foreground)):
                    seen = [osc(window_id, session_id, query) for _ in range(3)]
                    successful = [value for value in seen if value is not None]
                    assert successful and successful[-1] == expected, (
                        f"{what}: session {session_id} reports OSC {query} {seen}, not the committed {expected}")

        def picker():
            return named(app, "Select Theme", role="frame")

        def open_picker(window_title=None):
            run_palette_action(app, process.pid, window_title, "Select Theme…")
            frame = wait_for(picker, "Select Theme… did not open the theme picker")
            focus_accessible_window(frame, process.pid)
            return frame

        def keys(*names):
            subprocess.run(["xdotool", "key", "--clearmodifiers", "--delay", "5", *names], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        assert_committed("the launch")

        # Negative arm: an undisturbed preview really repaints, so the reverts below are observable.
        open_picker()
        keys("Down", "Down", "Down")
        time.sleep(1)
        session = open_sessions()[0]
        assert wait_for(lambda: (value := osc(*session, 11)) and value != THEMES[COMMITTED][0], "",
                        timeout=15, required=False), "a settled theme preview never reached the terminal"
        keys("Escape")
        wait_for(lambda: not picker(), "Escape did not close the theme picker")
        assert_committed("Escape after a settled preview")

        open_picker()
        keys("Down", "Down", "Down", "Escape")
        wait_for(lambda: not picker(), "Escape inside the debounce did not close the theme picker")
        assert_committed("Escape inside the preview debounce")

        # Alt+F4 is Openbox's Close binding: a real WM_DELETE_WINDOW, not an in-app key.
        open_picker()
        keys("Down", "Down", "Down", "alt+F4")
        wait_for(lambda: not picker(), "a window-manager close did not destroy the theme picker")
        assert_committed("a window-manager close inside the preview debounce")

        second_window = control_json(env, "window", "new", "theme-parent", "--json")["result"]["id"]
        second_session = window_tree(env, second_window)["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "rename", "theme-b", "--target", second_session,
                     "--window", second_window, "--json")
        title = wait_for(lambda: next((frame.get_name() for frame in collect(app, role="frame")
                                       if (frame.get_name() or "").startswith("theme-b")), None),
                         "the second window never took its session title")
        open_picker(title)
        keys("Down", "Down", "Down")
        control_json(env, "window", "close", second_window, "--json")
        wait_for(lambda: not picker(), "closing the parent window left its theme picker up")
        wait_for(lambda: all(item["id"] != second_window or not item["open"] for item in window_list(env)),
                 "the parent window did not close")
        assert_committed("a parent-window close with a pending preview")
        assert first_window in [item["id"] for item in window_list(env) if item["open"]]
        print("OK: Escape, a WM close and a parent close all revert a pending theme preview")
    finally:
        stop(process)
