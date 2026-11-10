"""Preferences driving shared by the zoom, preferences and chrome scenarios.

An `AdwComboRow` is changed the way a keyboard user changes it: the row is activated, which pops its list
up, and the list is walked with arrow keys. The popover is its own X11 surface, so its items' WINDOW
extents are relative to that surface and a pointer aimed through `mouse_click` would land on the dialog.
`settings.json` is the only proof a choice landed; every helper returns only once the file says so.
"""

import json
import os
import time

from atspi_smoke import (actionable, activate, collect, mouse_click, press_ctrl_comma, press_escape,
                         press_x11_key, preferences_window, wait_for)


def settings_path(env):
    return os.path.join(env["AGTERM_STATE_DIR"], "settings.json")


def read_settings(env):
    try:
        with open(settings_path(env), encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError):
        return {}


def seed_settings(env, **values):
    """Merge `values` into settings.json before a launch, keeping whatever a first run already wrote."""
    settings = read_settings(env)
    settings.update(values)
    with open(settings_path(env), "w", encoding="utf-8") as target:
        json.dump(settings, target)


def open_preferences(app, process_id, page=None):
    if not preferences_window(app):
        press_ctrl_comma(process_id)
    wait_for(lambda: preferences_window(app), "Ctrl+, did not open Preferences")
    if page:
        show_preferences_page(app, page)


def show_preferences_page(app, page):
    switcher = wait_for(lambda: actionable(preferences_window(app) or app, page),
                        f"Preferences has no actionable {page!r} page switcher")
    activate(switcher)
    time.sleep(0.5)


def close_preferences(app, process_id):
    press_escape(process_id)
    wait_for(lambda: not preferences_window(app), "Escape did not close Preferences")


def preferences_row(app, title):
    """The row carrying `title`, never the plain label inside it."""
    dialog = preferences_window(app)
    rows = [node for node in collect(dialog or app, name=title) if node.get_role_name() != "label"]
    return rows[0] if rows else None


def choose_combo(env, app, process_id, title, values, index, landed):
    """Pick `values[index]` in the combo row `title`, then wait until `landed(settings)` holds."""
    row = wait_for(lambda: preferences_row(app, title), f"Preferences has no {title!r} row")
    try:
        has_action = bool(row.get_action_iface() and row.get_action_iface().get_n_actions() > 0)
    except Exception:
        has_action = False
    if has_action:
        activate(row)
    else:
        mouse_click(lambda: preferences_row(app, title), process_id, button="left")

    def listed():
        # The row itself shows its current value; an open popover adds one label per value.
        return sum(1 for node in collect(app, role="label") if node.get_name() in values) >= 2

    wait_for(listed, f"activating the {title!r} row did not pop up its list")
    # Down enters the list wherever the popover put focus, Home pins the walk to the first value.
    press_x11_key("Down", process_id)
    press_x11_key("Home", process_id)
    for _ in range(index):
        press_x11_key("Down", process_id)
    press_x11_key("Return", process_id)
    wait_for(lambda: landed(read_settings(env)),
             f"choosing {values[index]!r} for {title!r} never reached settings.json: {read_settings(env)}")
