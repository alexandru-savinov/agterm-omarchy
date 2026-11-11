"""Appearance > Terminal defaults > Reset clears exactly the seven terminal-appearance fields.

Reset is a load-modify-save, so every other key must come back byte for byte, both straight after the
reset and after a relaunch has loaded and re-saved the file. A zoomed session must land on the effective
base size, which with the UI setting cleared is the scoped ghostty.conf's `font-size`, not a hardcoded 13.
Its zoom must also leave the persisted window snapshot, and dashboard tiles open in another window must
keep the dashboard's size.
"""

import json
import os
import time

from atspi_smoke import activate, collect, control_json, launch, stop, wait_for, window_list, window_tree
from atspi_prefs_combo import preferences_row, read_settings, seed_settings, settings_path

RESET_FIELDS = {"fontFamily", "fontSize", "cursorStyle", "cursorBlink", "theme", "darkTheme",
                "followSystemAppearance"}
SEEDED_APPEARANCE = dict(fontFamily="Reset Probe Mono", fontSize=17, cursorStyle="underline", cursorBlink=False,
                         theme="Dracula", darkTheme="Atom One Light", followSystemAppearance=True)
SEEDED_UNRELATED = dict(sidebarFontSize=11, interfaceFontSize=15, activeStatusColorHex="#123456",
                        blockedStatusShape="square", toolbarMode="normal", notificationsEnabled=False,
                        rightClickPaste=False, hiddenInterfaceElements=["focusFilter"], statusReset="anyKey",
                        # Seeded so the restore-mode migration has nothing to add on the reset's save.
                        restoreMode="none")
DEFAULT_THEME = "agterm"
# Distinct from Ghostty's 13 and the seeded 17, so landing on it proves the reset read the effective config.
SCOPED_FONT_SIZE = 15
# Far from every size above, so a tile the reset touched cannot pass for one it left alone.
DASHBOARD_FONT = 7


def saved_font_sizes(env, window_ids):
    """{session id: persisted fontSize} from every windows/<id>.json, the files the app writes
    (`WindowLibrary.persistenceStore`; workspaces.json is only a legacy migration source). Each listed window
    must have its file, and a missing or unreadable one fails: an absent snapshot never reads as "nothing
    persisted"."""
    windows_dir = os.path.join(env["AGTERM_STATE_DIR"], "windows")
    names = [name for name in os.listdir(windows_dir) if name.endswith(".json")] if os.path.isdir(windows_dir) else []
    for window_id in window_ids:
        assert f"{window_id.lower()}.json" in [name.lower() for name in names], (
            f"no windows/<id>.json for window {window_id} in {windows_dir}: {names}")
    found = {}
    for name in names:
        path = os.path.join(windows_dir, name)
        try:
            with open(path, encoding="utf-8") as source:
                saved = json.load(source)
        except (OSError, ValueError) as error:
            raise AssertionError(f"the window snapshot {path} could not be read: {error}") from error
        found.update({item["id"].lower(): item.get("fontSize") for workspace in saved.get("workspaces", [])
                      for item in workspace.get("sessions", [])})
    return found


def unrelated(settings):
    return {key: json.dumps(value, sort_keys=True) for key, value in settings.items() if key not in RESET_FIELDS}


def verify_appearance_reset(env):
    seed_settings(env, **SEEDED_APPEARANCE, **SEEDED_UNRELATED)
    config_dir = os.path.join(env["AGTERM_STATE_DIR"], "config")
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "ghostty.conf"), "w", encoding="utf-8") as target:
        target.write(f"font-size = {SCOPED_FONT_SIZE}\n")
    process, app = launch(dict(env, AGTERM_ATSPI_OPEN_PREFERENCES="appearance"))
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        session_id = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]

        def session_font():
            return window_tree(env, window_id)["workspaces"][0]["sessions"][0].get("fontSize")

        launched = wait_for(session_font, "the session never reported a font size")
        assert launched == SEEDED_APPEARANCE["fontSize"], (
            f"the UI font size should override the scoped config at launch, got {launched}")
        control_json(env, "font", "inc", "--target", session_id, "--window", window_id, "--json")
        zoomed = wait_for(lambda: (size := session_font()) != launched and size, "font inc did not zoom the session")
        # Positive control: the zoom reaches the window's file (after the save debounce), so the read after
        # the reset really reads the file the app writes.
        def zoom_saved():
            try:
                saved = saved_font_sizes(env, [window_id]).get(session_id.lower())
            except AssertionError:
                return False   # the file may not be written yet; a timeout still fails
            return saved is not None and abs(saved - zoomed) <= 0.5

        wait_for(zoom_saved, f"the zoomed size {zoomed} never reached this window's windows/<id>.json")

        # A dashboard in a second window: its tiles hold an override the reset must leave alone, because
        # nothing reapplies it until the dashboard is reopened.
        dash_window = control_json(env, "window", "new", "reset-dashboard", "--json")["result"]["id"]
        dash_first = window_tree(env, dash_window)["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "new", "--name", "reset-tile", "--window", dash_window, "--json")

        def dash_sessions():
            return {item["id"]: item for workspace in window_tree(env, dash_window)["workspaces"]
                    for item in workspace["sessions"]}

        tiles = list(wait_for(lambda: len(dash_sessions()) == 2 and dash_sessions(),
                              "the dashboard window never got its second session"))
        control_json(env, "dashboard", *tiles, "--font-size", str(DASHBOARD_FONT), "--window", dash_window, "--json")
        wait_for(lambda: window_tree(env, dash_window).get("dashboardMembers"), "the dashboard did not open")

        def tile_fonts():
            return {tile: dash_sessions()[tile].get("fontSize") for tile in tiles}

        # Vacuity guard: the override must really be on the tiles, or keeping it below means nothing.
        wait_for(lambda: all(size is not None and abs(size - DASHBOARD_FONT) <= 0.5 for size in tile_fonts().values()),
                 f"the {DASHBOARD_FONT}pt dashboard never reached its tiles: {tile_fonts()}")
        assert dash_first in tiles

        before = read_settings(env)
        for key, value in SEEDED_APPEARANCE.items():
            assert before.get(key) == value, f"the seeded {key} did not survive the launch: {before.get(key)!r}"
        kept = unrelated(before)
        assert set(SEEDED_UNRELATED) <= set(kept), f"seeded unrelated keys were dropped at launch: {sorted(kept)}"

        row = wait_for(lambda: preferences_row(app, "Terminal defaults"), "Appearance has no Terminal defaults row")
        reset = wait_for(lambda: next((item for item in collect(row, name="Reset")
                                       if item.get_role_name() != "label"), None),
                         "Terminal defaults has no Reset button")
        activate(reset)

        def reset_landed():
            settings = read_settings(env)
            return settings if settings.get("theme") == DEFAULT_THEME else None

        after = wait_for(reset_landed, f"Reset did not restore the {DEFAULT_THEME!r} theme: {read_settings(env)}")
        for key in RESET_FIELDS - {"theme"}:
            assert after.get(key) is None, f"Reset left {key} = {after.get(key)!r}"
        assert unrelated(after) == kept, (
            f"Reset changed unrelated keys: before {kept}, after {unrelated(after)}")
        wait_for(lambda: session_font() == SCOPED_FONT_SIZE,
                 f"Reset left the session font at {session_font()} (zoomed {zoomed}), not {SCOPED_FONT_SIZE}")
        # The same loop resets every window, so by now a tile the reset touched has already moved.
        time.sleep(1)
        assert window_tree(env, dash_window).get("dashboardMembers"), "the dashboard closed during the reset"
        assert all(size is not None and abs(size - DASHBOARD_FONT) <= 0.5 for size in tile_fonts().values()), (
            f"Reset re-sized the open dashboard's tiles: {tile_fonts()}, not {DASHBOARD_FONT}")
        # Also outlasts the debounced save, so a late re-pin of the reset size has landed before this read.
        saved = saved_font_sizes(env, [window_id, dash_window])
        missing = {session_id.lower(), *(tile.lower() for tile in tiles)} - set(saved)
        assert not missing, f"the window snapshots do not list the test sessions {sorted(missing)}: {sorted(saved)}"
        assert all(size is None for size in saved.values()), f"Reset left a persisted per-session font size: {saved}"
        print(f"measured: tiles {tile_fonts()} | windows/<id>.json fontSize {saved}")

        stop(process)
        process = None
        process, app = launch(env)
        wait_for(lambda: window_list(env), "the relaunch never registered a window")
        reloaded = read_settings(env)
        assert unrelated(reloaded) == kept, f"a relaunch changed unrelated keys: {unrelated(reloaded)}"
        returned = {key for key in RESET_FIELDS - {"theme"} if reloaded.get(key) is not None}
        assert reloaded.get("theme") == DEFAULT_THEME and not returned, (
            f"a relaunch brought reset appearance keys back: {reloaded}")
        with open(settings_path(env), encoding="utf-8") as source:
            print(f"measured: settings after reset+relaunch {source.read()}")
        print("OK: Terminal defaults > Reset clears the seven appearance fields and nothing else")
    finally:
        if process is not None:
            stop(process)
