"""Title-bar and sidebar-footer chrome stays reachable by keyboard and AT-SPI without taking the keyboard.

A pointer click must leave the keyboard on a terminal (Space then must not re-fire the button), Shift+Tab
from the search entry must reach every visible button, each must activate through AT-SPI, and each must
follow its Settings > Interface switch.
"""

import time

from gi.repository import Atspi

from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, ChromeFocus, actionable, activate, control_json, describe_tree,
                         mouse_click, press_x11_key, stop, wait_for)
from atspi_prefs_combo import close_preferences, open_preferences, preferences_row, read_settings

# element raw value, Interface switch title, accessible name
ELEMENTS = [
    ("sidebarToggle", "Sidebar toggle", "Toggle Sidebar (Ctrl+Shift+S)"),
    ("recentSessions", "Recent sessions", "Recent Sessions (Ctrl+Tab)"),
    ("scratch", "Scratch terminal", "Scratch Terminal (Ctrl+Shift+J)"),
    ("split", "Split view", "Toggle Split (Ctrl+Shift+D)"),
    ("dashboard", "Dashboard", "Dashboard (Ctrl+Shift+M)"),
    ("quickTerminal", "Quick terminal", "Quick Terminal (Ctrl+`)"),
    ("newWorkspace", "New workspace", "New Workspace"),
    ("newSession", "New session", "New Session"),
    ("flaggedView", "Flagged view", "Show Flagged Only"),
    ("focusFilter", "Workspace filter", "Show Only Focused Workspaces"),
]


def states(node):
    try:
        state = node.get_state_set()
        return {name: state.contains(kind) for name, kind in (
            ("showing", Atspi.StateType.SHOWING), ("enabled", Atspi.StateType.ENABLED),
            ("sensitive", Atspi.StateType.SENSITIVE), ("focusable", Atspi.StateType.FOCUSABLE))}
    except Exception:
        return {}


def verify_chrome_interface_controls(env):
    ctx = ChromeFocus(env)
    try:
        ctx.setup()
        app, pid = ctx.app, ctx.process.pid

        def button(name):
            return actionable(app, name)

        def session_count():
            return len(ctx.sessions())

        # Recent Sessions is insensitive with nothing to switch to, so give it a second session.
        control_json(env, "session", "new", "--name", "chrome-second", "--json")
        wait_for(lambda: len(ctx.sessions()) == 2, "the second session never appeared")
        ctx.select_session(ctx.session_id, "the original session could not be reselected")

        # Every element is present, sensitive and focusable with nothing hidden.
        table = []
        for raw, _, name in ELEMENTS:
            node = wait_for(lambda name=name: button(name), f"the {name!r} button is not actionable")
            state = states(node)
            table.append(f"{raw}={name!r} {state}")
            assert state.get("showing"), f"{name!r} is not shown: {state}"
            # Recent Sessions and the workspace filter are legitimately insensitive with nothing to act on.
            if raw not in ("recentSessions", "focusFilter"):
                assert state.get("sensitive"), f"{name!r} is not sensitive: {state}"
            assert state.get("focusable"), f"{name!r} is not focusable, so Tab cannot reach it: {state}"
        print("measured: " + " | ".join(table))

        # Toggle effects read off the tree, each paired with the control call that undoes it.
        def has_split():
            return any(item["id"] == ctx.session_id and item.get("hasSplit") for item in ctx.sessions())

        def has_scratch():
            return any(item["id"] == ctx.session_id and item.get("scratch") for item in ctx.sessions())

        toggles = {
            "Toggle Split (Ctrl+Shift+D)": (has_split, ("session", "split", "off", "--target", ctx.session_id)),
            "Scratch Terminal (Ctrl+Shift+J)": (has_scratch, ("session", "scratch", "off", "--target", ctx.session_id)),
            "Quick Terminal (Ctrl+`)": (lambda: ctx.tree().get("quickVisible"), ("quick", "hide")),
        }
        for name, (effect, undo) in toggles.items():
            mouse_click(lambda name=name: button(name), pid, button="left")
            wait_for(effect, f"clicking {name!r} did not take effect")
            press_x11_key("space", pid)
            time.sleep(NEGATIVE_SETTLE_SECONDS)
            assert effect(), f"Space after clicking {name!r} toggled it back: the button kept the keyboard"
            owner = ctx.owner_after(f"click-{name.split()[0].lower()}")
            assert owner and owner.startswith("owner="), f"no terminal took the keyboard after clicking {name!r}"
            control_json(env, *undo, "--json")
            wait_for(lambda: not effect(), f"undoing {name!r} over the control socket did not apply")

        sessions = session_count()
        mouse_click(lambda: button("New Session"), pid, button="left")
        wait_for(lambda: session_count() == sessions + 1, "clicking New Session did not add a session")
        press_x11_key("space", pid)
        time.sleep(NEGATIVE_SETTLE_SECONDS)
        assert session_count() == sessions + 1, "Space after clicking New Session added another: it kept the keyboard"
        owner = ctx.owner_after("click-footer")
        assert owner and owner.startswith("owner="), "no terminal took the keyboard after clicking New Session"
        ctx.select_session(ctx.session_id, "the original session could not be reselected")

        # Shift+Tab walks backward from the search entry through every visible chrome button.
        reached = set()
        ctx.open_search("to enter the chrome")
        for _ in range(60):
            press_x11_key("shift+Tab", pid)
            time.sleep(0.3)
            reached |= {name for _, _, name in ELEMENTS if ctx.has_keyboard(button(name))}
            if len(reached) == len(ELEMENTS):
                break
        missing = [name for _, _, name in ELEMENTS if name not in reached]
        assert not missing, f"Shift+Tab never reached {missing}"
        ctx.close_search("after the Tab walk")

        # AT-SPI activation reaches the same handlers a click does.
        for name, (effect, undo) in toggles.items():
            activate(button(name))
            wait_for(effect, f"AT-SPI activation of {name!r} did not take effect")
            control_json(env, *undo, "--json")
            wait_for(lambda: not effect(), f"undoing {name!r} did not apply")
        activate(button("Toggle Sidebar (Ctrl+Shift+S)"))
        wait_for(lambda: not ctx.tree().get("sidebarVisible"), "AT-SPI activation of Toggle Sidebar did not hide it")
        control_json(env, "sidebar", "show", "--json")
        wait_for(lambda: ctx.tree().get("sidebarVisible"), "the sidebar did not come back")
        activate(button("Show Flagged Only"))
        wait_for(lambda: ctx.tree().get("sidebarMode") == "flagged", "AT-SPI activation of Show Flagged Only failed")
        control_json(env, "sidebar", "mode", "tree", "--json")
        wait_for(lambda: ctx.tree().get("sidebarMode") == "tree", "the sidebar did not return to tree mode")

        # Each Interface switch hides exactly its button, and turning it back on restores it.
        open_preferences(app, pid, "Interface")
        for raw, title, name in ELEMENTS:
            row = wait_for(lambda title=title: preferences_row(app, title), f"Interface has no {title!r} switch")
            activate(row)
            wait_for(lambda raw=raw: raw in (read_settings(env).get("hiddenInterfaceElements") or []),
                     f"turning {title!r} off did not persist")
            wait_for(lambda name=name: button(name) is None, f"{name!r} stayed visible with {title!r} off")
            others = [other for _, _, other in ELEMENTS if other != name and button(other) is None]
            assert not others, f"turning {title!r} off also hid {others}"
            activate(preferences_row(app, title))
            wait_for(lambda raw=raw: raw not in (read_settings(env).get("hiddenInterfaceElements") or []),
                     f"turning {title!r} back on did not persist")
            wait_for(lambda name=name: button(name), f"{name!r} did not return with {title!r} on")
        close_preferences(app, pid)
        print("OK: chrome buttons keep the keyboard on the terminal, are Tab- and AT-SPI-reachable, and follow "
              "Settings > Interface")
    except AssertionError:
        describe_tree(ctx.app)
        raise
    finally:
        stop(ctx.process)
