"""A dashboard's cell font is a per-surface override that every exit must take back off.

Each grid is read from the session's own pty while that session is SELECTED, so its pane is mapped and
sized normally: the only thing left that can change its grid between two reads is the font.
"""

import json
import os
import time

from atspi_smoke import (activate, named, control_json, launch, press_escape, press_return, stop,
                         wait_for, window_list, window_tree)
from atspi_prefs_combo import read_settings
from atspi_zoom_ratio import pane_grid

DASHBOARD_FONT = 7


def font_sizes(node):
    """Every `fontSize` value anywhere in a decoded JSON document."""
    if isinstance(node, dict):
        found = [value for key, value in node.items() if key == "fontSize"]
        return found + [size for value in node.values() for size in font_sizes(value)]
    if isinstance(node, list):
        return [size for value in node for size in font_sizes(value)]
    return []


def verify_dashboard_font_restore(env):
    process, app = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        samples = iter(range(1000))

        def sessions():
            return {item["name"]: item for workspace in window_tree(env, window_id)["workspaces"]
                    for item in workspace["sessions"]}

        def new_session(name):
            control_json(env, "session", "new", "--name", name, "--window", window_id, "--json")
            return wait_for(lambda: sessions().get(name), f"session {name!r} never appeared")["id"]

        def select(session_id):
            control_json(env, "session", "select", "--target", session_id, "--window", window_id, "--json")
            wait_for(lambda: any(item["id"] == session_id and item["active"] for item in sessions().values()),
                     f"session {session_id} was not selected")

        def grid(session_id):
            select(session_id)
            return pane_grid(env, session_id, "left", f"font-{next(samples)}", window_id)

        def settled_grid(session_id, expected, what):
            seen = []

            def matches():
                seen.append(grid(session_id))
                return seen[-1] == expected
            if not wait_for(matches, "", timeout=25, required=False):
                raise AssertionError(f"{what}: the grid reads {seen[-1]}, not the pre-dashboard {expected}")

        pinned = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "rename", "font-pinned", "--target", pinned, "--window", window_id, "--json")
        default = new_session("font-default")
        select(pinned)
        unpinned_grid = grid(pinned)
        unpinned_size = sessions()["font-pinned"]["fontSize"]
        for _ in range(3):
            control_json(env, "font", "inc", "--target", pinned, "--window", window_id, "--json")
        pinned_size = wait_for(lambda: (size := sessions()["font-pinned"]["fontSize"]) == unpinned_size + 3 and size,
                               "the three font increments did not reach the live surface")
        pinned_grid = wait_for(lambda: (sample := grid(pinned)) != unpinned_grid and sample,
                               "font inc did not change the pinned session's grid")
        default_grid = grid(default)
        print(f"measured: pinned {pinned_grid} | default {default_grid} | unpinned {unpinned_grid}")

        # The session tree is persisted per window to windows/<id>.json (`WindowLibrary.persistenceStore`);
        # workspaces.json is only a legacy migration source the app never writes.
        windows_dir = os.path.join(env["AGTERM_STATE_DIR"], "windows")

        def saved_font_sizes():
            """{session id: persisted fontSize} from this window's own file. A missing or unreadable file
            fails: an absent snapshot must never read as "nothing persisted"."""
            names = [name for name in (os.listdir(windows_dir) if os.path.isdir(windows_dir) else [])
                     if name.lower() == f"{window_id.lower()}.json"]
            assert names, f"no windows/<id>.json for window {window_id} in {windows_dir}"
            path = os.path.join(windows_dir, names[0])
            try:
                with open(path, encoding="utf-8") as source:
                    saved = json.load(source)
            except (OSError, ValueError) as error:
                raise AssertionError(f"the window snapshot {path} could not be read: {error}") from error
            found = {item["id"].lower(): item.get("fontSize") for workspace in saved.get("workspaces", [])
                     for item in workspace.get("sessions", [])}
            assert pinned.lower() in found and default.lower() in found, (
                f"{path} does not list both test sessions: {sorted(found)}")
            return found

        # A truthy value can still be the pre-increment size while the 0.3 s save is pending.
        pinned_saved = wait_for(lambda: (size := saved_font_sizes()[pinned.lower()]) == pinned_size and size,
                                f"the pinned session's font size never reached {windows_dir}")
        before_settle = saved_font_sizes()
        time.sleep(1)
        baseline = saved_font_sizes()
        print(f"measured: before dashboard, snapshots before/after settle {before_settle} -> {baseline}; "
              f"default live fontSize {sessions()['font-default'].get('fontSize')}", flush=True)
        assert baseline[pinned.lower()] == pinned_saved, f"the pinned baseline changed before dashboard: {baseline}"
        assert baseline[default.lower()] is None, (
            f"the default session already holds a persisted size before dashboard: {baseline}")
        assert pinned_saved != DASHBOARD_FONT, f"the pinned size {pinned_saved} equals the dashboard size"
        print(f"measured: windows/<id>.json holds pinned fontSize {pinned_saved}, default None")

        def assert_nothing_persisted(exit_name):
            assert DASHBOARD_FONT not in font_sizes(read_settings(env)), (
                f"exit by {exit_name} left the dashboard size in settings.json")
            # Outlast the debounced save, so a late write of the dashboard size has landed before the read.
            time.sleep(1)
            sizes = saved_font_sizes()
            print(f"measured: after {exit_name}, settled snapshot {sizes}; "
                  f"default live fontSize {sessions()['font-default'].get('fontSize')}", flush=True)
            assert sizes[pinned.lower()] == pinned_saved and sizes[default.lower()] is None, (
                f"exit by {exit_name} persisted {sizes}, not pinned {pinned_saved} and default None")

        def open_dashboard(*members):
            control_json(env, "dashboard", *members, "--font-size", str(DASHBOARD_FONT),
                         "--window", window_id, "--json")
            wait_for(lambda: window_tree(env, window_id).get("dashboardMembers"), "the dashboard did not open")

        def dashboard_closed():
            return not window_tree(env, window_id).get("dashboardMembers")

        def assert_restored(exit_name):
            wait_for(dashboard_closed, f"exit by {exit_name} did not close the dashboard")
            # The file first: a dashboard size leaked into `session.fontSize` would also hold the grid at
            # 7 pt, and this order makes the persistence check the one that names that regression.
            assert_nothing_persisted(exit_name)
            settled_grid(pinned, pinned_grid, f"after exit by {exit_name}, the session with its own font")
            settled_grid(default, default_grid, f"after exit by {exit_name}, the session on the default")

        # Vacuity guard: an open dashboard must really move the grid, or no restore below means anything.
        open_dashboard(pinned, default)
        sampled = pane_grid(env, default, "left", "font-open", window_id)
        assert sampled[1] > default_grid[1], (
            f"the {DASHBOARD_FONT}pt dashboard left the default session at {sampled}, so it never applied")
        press_return(process.pid)
        assert_restored("selection")

        open_dashboard(pinned, default)
        press_escape(process.pid)
        assert_restored("Escape")

        open_dashboard(pinned, default)
        activate(wait_for(lambda: named(app, "Exit Dashboard", role="button"),
                          "the dashboard has no Exit Dashboard button"))
        assert_restored("the close button")

        # Reconciliation closes the dashboard and reopens it for the survivors, so the member that goes
        # away proves the rebuild and the control close proves the reopened overrides come off too.
        spare = new_session("font-spare")
        open_dashboard(pinned, default, spare)
        control_json(env, "session", "close", "--target", spare, "--window", window_id, "--json")
        wait_for(lambda: window_tree(env, window_id).get("dashboardMembers") == [f"{pinned}:left", f"{default}:left"],
                 f"closing a member did not rebuild the dashboard: {window_tree(env, window_id).get('dashboardMembers')}")
        control_json(env, "dashboard", "--close", "--window", window_id, "--json")
        assert_restored("reconciliation")
        print("OK: dashboard font overrides come off on selection, Escape, the close button and reconcile")
    finally:
        stop(process)
