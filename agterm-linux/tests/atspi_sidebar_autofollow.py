"""A held press on a sidebar multi-selection against the idle auto-follow timer."""

import json
import os
import subprocess
import time

from atspi_smoke import (
    NUDGE_DYS, calibrate_row_click, control_json, describe_tree, launch, mouse_click, mouse_press_probe_release,
    poll, row_selected, sidebar_session_row_label, stop, wait_for, window_bounds, window_list, window_tree,
)

HOLD_SECONDS = 6.5


def verify_sidebar_multiselect_autofollow(env):
    with open(os.path.join(env["AGTERM_STATE_DIR"], "settings.json"), "w", encoding="utf-8") as target:
        json.dump({"autoFollowAttention": "s5"}, target)
    process, app = launch(env)
    try:
        window_id = wait_for(lambda: next((item["id"] for item in window_list(env) if item["open"]), None),
                             "initial window was not registered")
        control_json(env, "session", "rename", "af-one", "--window", window_id, "--json")
        for name in ("af-two", "af-three", "af-blocked"):
            control_json(env, "session", "new", "--name", name, "--window", window_id, "--json")

        def sessions():
            return window_tree(env, window_id)["workspaces"][0]["sessions"]

        def names():
            return [item["name"] for item in sessions()]

        def active_name():
            return next((item["name"] for item in sessions() if item.get("active")), None)

        def selected():
            return tuple(name for name in names() if row_selected(app, name))

        wait_for(lambda: names() == ["af-one", "af-two", "af-three", "af-blocked"],
                 f"the fixture sessions did not settle: {names()}")
        blocked_id = next(item["id"] for item in sessions() if item["name"] == "af-blocked")
        wait_for(lambda: sidebar_session_row_label(app, "af-blocked"), "the af-blocked row never appeared")
        row_dy = calibrate_row_click(app, process.pid, "af-three")

        def build_block():
            mouse_click(lambda: sidebar_session_row_label(app, "af-one"), process.pid, button="left", dy=row_dy)
            wait_for(lambda: active_name() == "af-one" and selected() == ("af-one",), "anchoring on af-one failed")
            for nudge in NUDGE_DYS:
                mouse_click(lambda: sidebar_session_row_label(app, "af-two"), process.pid, button="left",
                            dy=row_dy + nudge, modifier="shift")
                if poll(lambda: active_name() == "af-two" and selected() == ("af-one", "af-two"), timeout=3):
                    break
            else:
                raise AssertionError(f"shift-click never built the af-one..af-two block: {selected()}")
            control_json(env, "session", "status", "blocked", "--target", blocked_id, "--json")

        def hold(then=None):
            def probe():
                time.sleep(HOLD_SECONDS)
                seen = (active_name(), selected())
                if then:
                    then()
                return seen
            return probe

        build_block()
        held_active, held_selection = mouse_press_probe_release(
            lambda: sidebar_session_row_label(app, "af-two"), process.pid, hold(), dy=row_dy)
        assert (held_active, held_selection) == ("af-two", ("af-one", "af-two")), (
            f"auto-follow fired during a {HOLD_SECONDS}s press on the selection block: active {held_active!r}, "
            f"selection {held_selection}")
        wait_for(lambda: active_name() == "af-two" and selected() == ("af-two",),
                 f"releasing the held press did not collapse to af-two: active {active_name()!r}, {selected()}")

        control_json(env, "session", "status", "idle", "--target", blocked_id, "--json")
        build_block()

        def drag_to_bottom_of_blocked():
            geometry = subprocess.check_output(["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                                               text=True)
            origin = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
            source = window_bounds(lambda: sidebar_session_row_label(app, "af-two"), "drag source")
            target = window_bounds(lambda: sidebar_session_row_label(app, "af-blocked"), "drag target")
            start = (int(origin["X"]) + source.x + source.width // 2,
                     int(origin["Y"]) + source.y + source.height // 2 + row_dy)
            end = (int(origin["X"]) + target.x + target.width // 2,
                   int(origin["Y"]) + target.y + round(target.height * 0.75) + row_dy)
            for step in range(1, 13):
                point = (round(start[0] + (end[0] - start[0]) * step / 12),
                         round(start[1] + (end[1] - start[1]) * step / 12))
                subprocess.run(["xdotool", "mousemove", "--sync", str(point[0]), str(point[1])], check=True)
                time.sleep(0.03)
            time.sleep(0.3)

        held_active, held_selection = mouse_press_probe_release(
            lambda: sidebar_session_row_label(app, "af-two"), process.pid, hold(drag_to_bottom_of_blocked), dy=row_dy)
        wait_for(lambda: names() != ["af-one", "af-two", "af-three", "af-blocked"],
                 f"the held block drag moved nothing: {names()}")
        assert names() == ["af-three", "af-blocked", "af-one", "af-two"], (
            f"a block drag after a {HOLD_SECONDS}s hold dropped {names()} (held active {held_active!r}, "
            f"selection {held_selection}) instead of moving af-one and af-two below af-blocked")
        assert (held_active, held_selection) == ("af-two", ("af-one", "af-two")), (
            f"auto-follow replaced the block during the held drag: active {held_active!r}, {held_selection}")
        print(f"OK: a {HOLD_SECONDS}s press on a selection block outlasts the 5s auto-follow idle window; "
              "the release collapses to the pressed row and the drag drops the whole block")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)
