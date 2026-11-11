"""Physical cancellation restores terminal state and rendered chrome in two surviving windows.

Key timing does not establish a pending debounce. ThemePickerCancellationGTKTests observes the actual
timer seam and delivers stale callbacks explicitly; this scenario exercises real input and pixels.
"""

from collections import Counter
import itertools
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

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
        titles = {}

        def name_window(window_id, title):
            session = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]
            response = control_json(env, "session", "rename", title, "--target", session,
                                    "--window", window_id, "--json")
            assert response["ok"], response
            titles[window_id] = title

        name_window(first_window, "theme-survivor-a")
        response = control_json(env, "window", "new", "theme-survivor-b", "--json")
        assert response["ok"], response
        second_window = response["result"]["id"]
        name_window(second_window, "theme-survivor-b")
        survivors = (first_window, second_window)
        saved_settings = read_settings(env)
        artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", state)) / "theme-cancellation"
        artifacts.mkdir(parents=True, exist_ok=True)

        def frame_for(window_id):
            return wait_for(lambda: next((frame for frame in collect(app, role="frame")
                                         if (frame.get_name() or "").startswith(titles[window_id])), None),
                            f"window {titles[window_id]} never appeared")

        def chrome(window_id):
            frame = frame_for(window_id)
            matches = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--all", "--pid",
                                               str(process.pid), "--name", f"^{re.escape(frame.get_name())}$"],
                                              text=True, timeout=5).splitlines()
            clients = subprocess.check_output(["xprop", "-root", "_NET_CLIENT_LIST"], text=True, timeout=5)
            managed = {int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", clients)}
            matches = [value for value in matches if int(value) in managed]
            assert len(matches) == 1, f"ambiguous managed window for {titles[window_id]}: {matches}"
            xid = matches[0]
            assert subprocess.check_output(["xdotool", "getwindowpid", xid], text=True).strip() == str(process.pid)
            assert subprocess.check_output(["xdotool", "getwindowname", xid], text=True).startswith(titles[window_id])
            # Raise the exposed header without asking a modal picker's parent to take keyboard focus.
            subprocess.run(["xdotool", "windowraise", xid], check=True, timeout=5)
            image = artifacts / f"{titles[window_id]}-{next(tags)}.png"
            subprocess.run(["import", "-window", xid, str(image)], check=True, timeout=10)
            width = int(subprocess.check_output(["convert", str(image), "-format", "%w", "info:"], text=True))
            assert width > 200
            # A narrow strip of the actual headerbar avoids the terminal and title/buttons below it.
            pixels = subprocess.check_output(["convert", str(image), "-crop", f"{width - 80}x8+40+5",
                                              "+repage", "-alpha", "off", "-depth", "8", "rgb:-"], timeout=10)
            color, count = Counter(zip(pixels[::3], pixels[1::3], pixels[2::3])).most_common(1)[0]
            assert count > len(pixels) // 6, "headerbar strip has no dominant painted background"
            return "#" + "".join(f"{value:02x}" for value in color)

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
            assert read_settings(env) == saved_settings, f"{what} changed persisted settings"
            assert read_settings(env).get("theme") == COMMITTED, (
                f"{what} rewrote the saved theme to {read_settings(env).get('theme')!r}")
            background, foreground = THEMES[COMMITTED]
            for window_id, session_id in open_sessions():
                for query, expected in ((11, background), (10, foreground)):
                    seen = [osc(window_id, session_id, query) for _ in range(3)]
                    successful = [value for value in seen if value is not None]
                    assert successful and successful[-1] == expected, (
                        f"{what}: session {session_id} reports OSC {query} {seen}, not the committed {expected}")
            for window_id in survivors:
                assert window_id in [item["id"] for item in window_list(env) if item["open"]]
                assert wait_for(lambda: chrome(window_id) == background, "", timeout=5, required=False), (
                    f"{what}: {titles[window_id]} chrome did not restore {background}")

        def picker():
            return named(app, "Select Theme", role="frame")

        def open_picker(window_title=None):
            window_title = window_title or frame_for(first_window).get_name()
            run_palette_action(app, process.pid, window_title, "Select Theme…")
            frame = wait_for(picker, "Select Theme… did not open the theme picker")
            focus_accessible_window(frame, process.pid)
            return frame

        def keys(*names):
            subprocess.run(["xdotool", "key", "--clearmodifiers", "--delay", "5", *names], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        assert_committed("the launch")

        # Negative arm: an undisturbed preview really repaints, so the reverts below are observable.
        preview_frame = open_picker()
        keys("Down", "Down", "Down")
        time.sleep(1)
        session = open_sessions()[0]
        assert wait_for(lambda: (value := osc(*session, 11)) and value != THEMES[COMMITTED][0], "",
                        timeout=15, required=False), "a settled theme preview never reached the terminal"
        for window_id in survivors:
            assert wait_for(lambda: chrome(window_id) != THEMES[COMMITTED][0], "", timeout=5, required=False), (
                "a settled preview never changed the rendered chrome")
        focus_accessible_window(preview_frame, process.pid)
        keys("Escape")
        wait_for(lambda: not picker(), "Escape did not close the theme picker")
        assert_committed("Escape after a settled preview")

        open_picker()
        keys("Down", "Down", "Down", "Escape")
        wait_for(lambda: not picker(), "Escape after navigation did not close the theme picker")
        assert_committed("Escape after navigation")

        # Alt+F4 is Openbox's Close binding: a real WM_DELETE_WINDOW, not an in-app key.
        open_picker()
        keys("Down", "Down", "Down", "alt+F4")
        wait_for(lambda: not picker(), "a window-manager close did not destroy the theme picker")
        assert_committed("a window-manager close after navigation")

        parent_window = control_json(env, "window", "new", "theme-parent", "--json")["result"]["id"]
        second_session = window_tree(env, parent_window)["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "rename", "theme-b", "--target", second_session,
                     "--window", parent_window, "--json")
        title = wait_for(lambda: next((frame.get_name() for frame in collect(app, role="frame")
                                       if (frame.get_name() or "").startswith("theme-b")), None),
                         "the second window never took its session title")
        open_picker(title)
        keys("Down", "Down", "Down")
        closed = control_json(env, "window", "close", parent_window, "--json")
        assert closed["ok"], closed
        wait_for(lambda: not picker(), "closing the parent window left its theme picker up")
        wait_for(lambda: all(item["id"] != parent_window or not item["open"] for item in window_list(env)),
                 "the parent window did not close")
        assert_committed("a parent-window close after navigation")
        assert first_window in [item["id"] for item in window_list(env) if item["open"]]
        print("OK: Escape, WM close and parent close restore settings, OSC colors and chrome in two surviving windows")
    finally:
        stop(process)
