"""Toolbar and sidebar preference changes made DURING a terminal zoom must not undo it.

GTK drops a hidden widget from the traversable AT-SPI tree, so each piece of chrome is "shown" exactly
when one of its known accessibles is reachable: the content header by its Toggle Split button, the zoom
strip by Exit Terminal Zoom, the sidebar by the session's own row.
"""

import itertools

from atspi_smoke import (ChromeFocus, actionable, control_json, describe_tree, named, sidebar_session_row,
                         stop, wait_for)
from atspi_prefs_combo import choose_combo, close_preferences, open_preferences

TOOLBAR_VALUES = ["Normal", "Compact", "Hidden"]
TOOLBAR_SETTING = {"Normal": "normal", "Compact": None, "Hidden": "hidden"}


def verify_zoom_preference_changes(env):
    ctx = ChromeFocus(env)
    try:
        ctx.setup()
        name = "zoom-chrome"
        control_json(env, "session", "rename", name, "--target", ctx.session_id, "--json")
        wait_for(lambda: sidebar_session_row(ctx.app, name), "the renamed session row never appeared")
        app, pid = ctx.app, ctx.process.pid
        markers = itertools.count()

        def content_header():
            return actionable(app, "Toggle Split (Ctrl+Shift+D)") is not None

        def zoom_header():
            return named(app, "Exit Terminal Zoom", role="button") is not None

        def sidebar():
            return sidebar_session_row(app, name) is not None

        def assert_zoomed_chrome(toolbar, what):
            assert ctx.tree().get("zoomedSurface"), f"{what} ended the terminal zoom"
            wait_for(lambda: not content_header(), f"{what} brought the content header back over the zoom")
            wait_for(lambda: not sidebar(), f"{what} brought the sidebar back over the zoom")
            want = toolbar != "Hidden"
            wait_for(lambda: zoom_header() == want,
                     f"{what}: the zoom strip is {'hidden' if want else 'shown'} under toolbar {toolbar}")
            owner = ctx.owner_after(f"zoom-chrome-{next(markers)}")
            assert owner == ctx.session_owner, (
                f"after {what} the keyboard no longer reached the zoomed terminal (marker read {owner!r})")

        def set_toolbar(value):
            open_preferences(app, pid, "Appearance")
            choose_combo(env, app, pid, "Toolbar", TOOLBAR_VALUES, TOOLBAR_VALUES.index(value),
                         lambda settings: settings.get("toolbarMode") == TOOLBAR_SETTING[value])
            close_preferences(app, pid)

        def set_sidebar(mode):
            control_json(env, "sidebar", mode, "--json")
            wait_for(lambda: ctx.tree().get("sidebarVisible") == (mode == "show"), f"sidebar {mode} did not apply")

        assert content_header() and sidebar(), "the content header or sidebar was missing before the zoom"
        control_json(env, "surface", "zoom", "show", "--target", "active", "--json")
        wait_for(lambda: ctx.tree().get("zoomedSurface"), "the terminal zoom did not engage")
        assert_zoomed_chrome("Compact", "engaging the zoom")

        for value in ("Normal", "Hidden", "Compact"):
            set_toolbar(value)
            assert_zoomed_chrome(value, f"switching the toolbar to {value}")
        for mode in ("hide", "show", "hide"):
            set_sidebar(mode)
            assert_zoomed_chrome("Compact", f"sidebar {mode}")

        # The newest values, compact and hidden, are applied on exit, not the launch-time ones.
        control_json(env, "surface", "zoom", "hide", "--target", "active", "--json")
        wait_for(lambda: not ctx.tree().get("zoomedSurface"), "the terminal zoom did not exit")
        wait_for(content_header, "the compact toolbar did not bring the content header back after the zoom")
        assert not zoom_header(), "the zoom strip outlived the zoom"
        wait_for(lambda: not sidebar() and not ctx.tree().get("sidebarVisible"),
                 "the sidebar hidden during the zoom came back on exit")
        set_sidebar("show")
        wait_for(sidebar, "the sidebar did not show after the zoom")
        set_toolbar("Hidden")
        wait_for(lambda: not content_header(), "the hidden toolbar left the content header up after the zoom")
        owner = ctx.owner_after("zoom-chrome-exit")
        assert owner == ctx.session_owner, f"the terminal lost the keyboard after the zoom (marker {owner!r})"
        print("OK: toolbar and sidebar changes during a zoom keep it zoomed and apply on exit")
    except AssertionError:
        describe_tree(ctx.app)
        raise
    finally:
        stop(ctx.process)
