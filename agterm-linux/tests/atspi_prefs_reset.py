"""Appearance > Terminal defaults > Reset clears exactly the seven terminal-appearance fields.

Reset is a load-modify-save, so every other key must come back byte for byte, both straight after the
reset and after a relaunch has loaded and re-saved the file.
"""

import json
import os

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
DEFAULT_FONT_SIZE = 13


def font_sizes(node):
    """Every `fontSize` value anywhere in a decoded JSON document."""
    if isinstance(node, dict):
        found = [value for key, value in node.items() if key == "fontSize"]
        return found + [size for value in node.values() for size in font_sizes(value)]
    if isinstance(node, list):
        return [size for value in node for size in font_sizes(value)]
    return []


def unrelated(settings):
    return {key: json.dumps(value, sort_keys=True) for key, value in settings.items() if key not in RESET_FIELDS}


def verify_appearance_reset(env):
    seed_settings(env, **SEEDED_APPEARANCE, **SEEDED_UNRELATED)
    process, app = launch(dict(env, AGTERM_ATSPI_OPEN_PREFERENCES="appearance"))
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        session_id = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]

        def session_font():
            return window_tree(env, window_id)["workspaces"][0]["sessions"][0].get("fontSize")

        launched = wait_for(session_font, "the session never reported a font size")
        control_json(env, "font", "inc", "--target", session_id, "--window", window_id, "--json")
        zoomed = wait_for(lambda: (size := session_font()) != launched and size, "font inc did not zoom the session")

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
        wait_for(lambda: session_font() == DEFAULT_FONT_SIZE,
                 f"Reset left the session font at {session_font()} (zoomed {zoomed}), not {DEFAULT_FONT_SIZE}")
        with open(os.path.join(env["AGTERM_STATE_DIR"], "workspaces.json"), encoding="utf-8") as source:
            saved = json.load(source)
        assert not [size for size in font_sizes(saved) if size is not None], (
            "Reset left a per-session font size in the saved tree")

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
