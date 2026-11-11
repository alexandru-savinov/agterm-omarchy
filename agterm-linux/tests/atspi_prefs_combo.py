"""Preferences driving shared by the zoom, preferences and chrome scenarios.

An `AdwComboRow` is changed the way a keyboard user changes it: the row is activated, which pops its list
up, and the list is walked with arrow keys. The popover is its own X11 surface, so its items' WINDOW
extents are relative to that surface and a pointer aimed through `mouse_click` would land on the dialog.
`settings.json` is the only proof a choice landed; every helper returns only once the file says so.
"""

import json
import os
import time

from gi.repository import Atspi

from atspi_smoke import (activate, collect, mouse_click, press_ctrl_comma, press_escape,
                         press_x11_key, preferences_window, type_x11_text, wait_for)


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


def label_counts(app):
    counts = {}
    for node in collect(app, role="label"):
        name = node.get_name() or ""
        counts[name] = counts.get(name, 0) + 1
    return counts


def item_ancestry(app, text):
    """(role, action count, has selection, focused) up from each label named `text`, for a failure report."""
    chains = []
    for node in collect(app, role="label", name=text):
        chain, current = [], node
        for _ in range(6):
            try:
                actions = current.get_action_iface()
                chain.append((current.get_role_name(), actions.get_n_actions() if actions else 0,
                              bool(current.get_selection_iface()),
                              current.get_state_set().contains(Atspi.StateType.FOCUSED)))
                current = current.get_parent()
            except Exception:
                break
            if current is None:
                break
        chains.append(chain)
    return chains


def pick_through_atspi(app, text, landed):
    """Select the popover item labelled `text` through its list's AT-SPI selection, else its own action."""
    for node in collect(app, role="label", name=text):
        child, parent = node, node.get_parent()
        for _ in range(4):
            if parent is None:
                break
            try:
                selection = parent.get_selection_iface()
                if selection and selection.select_child(child.get_index_in_parent()) and wait_for(
                        landed, "", timeout=3, required=False):
                    return True
                actions = child.get_action_iface()
                if actions and actions.get_n_actions() > 0 and actions.do_action(0) and wait_for(
                        landed, "", timeout=3, required=False):
                    return True
            except Exception:
                pass
            child, parent = parent, parent.get_parent()
    return False


def focused_node(app):
    for node in collect(app):
        try:
            if node.get_state_set().contains(Atspi.StateType.FOCUSED):
                return (node.get_role_name(), node.get_name())
        except Exception:
            pass
    return None


def choose_combo(env, app, process_id, title, values, index, landed, search=None):
    """Pick `values[index]` in the combo row `title`, then wait until `landed(settings)` holds.

    `search(new_labels)` is for a combo with a search entry (libadwaita adds one past ten values): it gets
    the labels the popover added and returns the text to type. Such a popover opens with the keyboard in
    its entry, so the plain arrow walk is tried first, then AT-SPI selection, then typing the value.
    """
    wait_for(lambda: preferences_row(app, title), f"Preferences has no {title!r} row")
    before = label_counts(app)

    def added():
        after = label_counts(app)
        return [name for name, count in after.items() if name and count > before.get(name, 0)]

    def popped():
        if search:
            # The row already shows one of `values`, so only a label beyond them proves the list is up.
            return any(name not in values for name in added())
        # Only labels the popover ADDED count: other rows on the page can already show these same values.
        return len(added()) >= 2

    def pop_up():
        if popped():
            return
        node = preferences_row(app, title)
        try:
            has_action = bool(node.get_action_iface() and node.get_action_iface().get_n_actions() > 0)
        except Exception:
            has_action = False
        if has_action:
            activate(node)
        else:
            mouse_click(lambda: preferences_row(app, title), process_id, button="left")
        wait_for(popped, f"activating the {title!r} row did not pop up its list")

    def arrow_walk():
        # Down enters the list wherever the popover put focus, Home pins the walk to the first value.
        press_x11_key("Down", process_id)
        press_x11_key("Home", process_id)
        for _ in range(index):
            press_x11_key("Down", process_id)
        press_x11_key("Return", process_id)

    pop_up()
    if not search:
        arrow_walk()
        wait_for(lambda: landed(read_settings(env)),
                 f"choosing entry {index} of {title!r} never reached settings.json: {read_settings(env)}")
        return

    text = search(added())
    assert text, f"the {title!r} popover offered nothing to pick: {added()}"
    report = [f"opened with focus on {focused_node(app)}", f"item ancestry {item_ancestry(app, text)}"]

    def typed():
        type_x11_text(text, process_id)
        time.sleep(1)
        report.append(f"after typing, focus on {focused_node(app)}")
        press_x11_key("Down", process_id)
        press_x11_key("Return", process_id)

    for name, attempt in (("arrows", arrow_walk),
                          ("atspi", lambda: pick_through_atspi(app, text, lambda: landed(read_settings(env)))),
                          ("search", typed)):
        pop_up()
        attempt()
        if wait_for(lambda: landed(read_settings(env)), "", timeout=4, required=False):
            print(f"measured: {title!r} combo picked by {name}")
            return
        report.append(f"{name} did not land")
        if popped():
            press_escape(process_id)
            wait_for(lambda: not popped(), "", timeout=3, required=False)
    raise AssertionError(f"picking {text!r} in {title!r} never reached settings.json: {read_settings(env)}; "
                         + "; ".join(report))
