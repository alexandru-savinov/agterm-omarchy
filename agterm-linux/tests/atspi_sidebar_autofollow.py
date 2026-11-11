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
            wait_for(lambda: next(item for item in sessions() if item["id"] == blocked_id).get("status") == "blocked",
                     "af-blocked never took the blocked status")
            time.sleep(0.5)
            assert (active_name(), selected()) == ("af-two", ("af-one", "af-two")), (
                f"a status on another row collapsed the block before any press: {active_name()!r}, {selected()}")

        def hold(then=None):
            def probe():
                start, timeline = time.monotonic(), []
                for mark in (0.6, 3.0, 5.6, HOLD_SECONDS):
                    time.sleep(max(0, start + mark - time.monotonic()))
                    timeline.append((mark, active_name(), selected()))
                if then:
                    then()
                return timeline
            return probe

        def intact(timeline):
            return all(sample[1:] == ("af-two", ("af-one", "af-two")) for sample in timeline)

        def short_press(name, marks=(0.05, 0.6, 1.5)):
            def probe():
                start, timeline = time.monotonic(), []
                for mark in marks:
                    time.sleep(max(0, start + mark - time.monotonic()))
                    timeline.append((mark, active_name(), selected()))
                return timeline
            before = (active_name(), selected())
            during = mouse_press_probe_release(lambda: sidebar_session_row_label(app, name), process.pid, probe,
                                               dy=row_dy)
            time.sleep(0.5)
            return before, during, (active_name(), selected())

        build_block()
        before = (active_name(), selected())
        timeline = mouse_press_probe_release(
            lambda: sidebar_session_row_label(app, "af-two"), process.pid, hold(), dy=row_dy)
        if not intact(timeline):
            # DIAGNOSTIC (temporary): discriminate which press collapses the block on press.
            print(f"DIAG held af-two: before {before}, timeline {timeline}, after {(active_name(), selected())}")
            build_block()
            print(f"DIAG press af-one (non-active member): {short_press('af-one')}")
            build_block()
            print(f"DIAG press af-two (active member): {short_press('af-two')}")
            control_json(env, "session", "status", "idle", "--target", blocked_id, "--json")
            time.sleep(0.3)
            mouse_click(lambda: sidebar_session_row_label(app, "af-one"), process.pid, button="left", dy=row_dy)
            time.sleep(0.8)
            mouse_click(lambda: sidebar_session_row_label(app, "af-two"), process.pid, button="left", dy=row_dy,
                        modifier="shift")
            time.sleep(1.0)
            print(f"DIAG press af-two, no blocked row: {short_press('af-two')}")
        assert intact(timeline), f"the selection block changed during a {HOLD_SECONDS}s held press: {timeline}"
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

        timeline = mouse_press_probe_release(
            lambda: sidebar_session_row_label(app, "af-two"), process.pid, hold(drag_to_bottom_of_blocked), dy=row_dy)
        wait_for(lambda: names() != ["af-one", "af-two", "af-three", "af-blocked"],
                 f"the held block drag moved nothing: {names()}")
        assert names() == ["af-three", "af-blocked", "af-one", "af-two"], (
            f"a block drag after a {HOLD_SECONDS}s hold dropped {names()} (held {timeline}) instead of moving "
            "af-one and af-two below af-blocked")
        assert intact(timeline), f"the block changed during the held drag: {timeline}"
        print(f"OK: a {HOLD_SECONDS}s press on a selection block outlasts the 5s auto-follow idle window; "
              "the release collapses to the pressed row and the drag drops the whole block")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)
