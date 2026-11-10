"""Preferences driving shared by the zoom, preferences and chrome scenarios.

An `AdwComboRow` is changed the way a keyboard user changes it: the row is activated, which pops its list
up, and the list is walked with arrow keys. The popover is its own X11 surface, so its items' WINDOW
extents are relative to that surface and a pointer aimed through `mouse_click` would land on the dialog.
`settings.json` is the only proof a choice landed; every helper returns only once the file says so.
"""

import json
import os

from atspi_smoke import (activate, collect, mouse_click, press_ctrl_comma, press_escape,
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
        show_preferences_page(app, page, process_id)


def show_preferences_page(app, page, process_id):
    """Switch the dialog to `page`, proven by the page's panel exposing its rows.

    Hidden pages stay in the tree as empty panels, so a populated panel is the switch's only evidence. The
    view switcher's tabs are tried through AT-SPI selection, then their action, then a real click.
    """
    def tab():
        dialog = preferences_window(app)
        return next((node for node in collect(dialog or app, name=page) if node.get_role_name() == "page tab"), None)

    def shown():
        dialog = preferences_window(app)
        for panel in collect(dialog or app, role="panel", name=page):
            if any(item.get_name() for item in collect(panel) if item != panel):
                return True
        return False

    def by_selection(node):
        parent = node.get_parent()
        selection = parent.get_selection_iface() if parent else None
        if selection:
            selection.select_child(node.get_index_in_parent())

    def by_action(node):
        actions = node.get_action_iface()
        if actions and actions.get_n_actions() > 0:
            actions.do_action(0)

    if shown():
        return
    wait_for(tab, f"Preferences has no {page!r} page tab")
    tried = []
    for name, attempt in (("selection", by_selection), ("action", by_action),
                          ("click", lambda node: mouse_click(tab, process_id, button="left"))):
        try:
            attempt(tab())
        except Exception as error:
            tried.append(f"{name}: {error}")
            continue
        if wait_for(shown, "", timeout=3, required=False):
            return
        tried.append(f"{name}: no switch")
    raise AssertionError(f"could not switch Preferences to {page!r} ({'; '.join(tried)})")


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
