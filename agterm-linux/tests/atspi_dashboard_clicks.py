"""A dashboard click enters its pane only after a short delay, and nothing may survive into that delay.

The app runs with `AGTERM_ATSPI_DASHBOARD_CLICK_DELAY_MS` widening the 180 ms window, so each cancelling
event below lands inside it by construction rather than by scheduler luck. The highlight read off the tree
is what proves a click was accepted and is pending.
"""

import time

from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, control_json, launch, mouse_click, named, press_escape,
                         press_x11_key, stop, wait_for, window_list, window_tree)

CLICK_DELAY_MS = 3000


def verify_dashboard_delayed_clicks(env):
    process, app = launch(dict(env, AGTERM_ATSPI_DASHBOARD_CLICK_DELAY_MS=str(CLICK_DELAY_MS)))
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])

        def tree():
            return window_tree(env, window_id)

        def sessions():
            return {item["name"]: item for workspace in tree()["workspaces"] for item in workspace["sessions"]}

        def active():
            return next((name for name, item in sessions().items() if item["active"]), None)

        first = tree()["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "rename", "click-a", "--target", first, "--window", window_id, "--json")
        ids = {"click-a": first}
        for name in ("click-b", "click-c"):
            ids[name] = control_json(env, "session", "new", "--name", name, "--window", window_id,
                                     "--json")["result"]["id"]
        control_json(env, "session", "split", "on", "--target", ids["click-c"], "--window", window_id, "--json")
        wait_for(lambda: sessions()["click-c"].get("hasSplit"), "click-c did not split")
        control_json(env, "session", "select", "--target", first, "--window", window_id, "--json")
        wait_for(lambda: active() == "click-a", "click-a was not selected")

        def open_dashboard():
            control_json(env, "dashboard", "--mru", "--window", window_id, "--json")
            wait_for(lambda: tree().get("dashboardMembers"), "the dashboard did not open")

        def click(name, pane):
            label = f"{name} · {pane}"
            wait_for(lambda: named(app, label, role="label"), f"the dashboard has no {label!r} cell")
            mouse_click(lambda: named(app, label, role="label"), process.pid, button="left")
            member = f"{ids[name]}:{pane.lower()}"
            wait_for(lambda: tree().get("dashboardHighlighted") == member,
                     f"clicking {label!r} did not highlight it, so no delayed click is pending")

        def assert_nothing_committed(what, still_open):
            time.sleep(max(NEGATIVE_SETTLE_SECONDS, CLICK_DELAY_MS / 1000 + 2))
            assert active() == "click-a", f"{what} still entered a pane: {active()} is active"
            assert not sessions()["click-a"].get("splitFocused"), f"{what} moved the focused pane"
            assert bool(tree().get("dashboardMembers")) == still_open, (
                f"{what} left the dashboard {'closed' if still_open else 'open'}")

        # Removal of the clicked member inside the delay: the rebuilt dashboard simply lacks it.
        open_dashboard()
        click("click-b", "Left")
        control_json(env, "session", "close", "--target", ids["click-b"], "--window", window_id, "--json")
        wait_for(lambda: "click-b" not in sessions(), "click-b did not close")
        members = wait_for(lambda: (members := tree().get("dashboardMembers")) and members,
                           "the dashboard did not rebuild after its member closed")
        assert not any(member.startswith(ids["click-b"]) for member in members), (
            f"the rebuilt dashboard still lists the closed member: {members}")
        assert_nothing_committed("a click on a member closed inside its delay", still_open=True)

        # A keyboard move inside the delay supersedes the pending click.
        click("click-c", "Left")
        # The highlight clamps at a grid edge, so try directions until one actually moves it.
        # Each key gets a bounded wait for the highlight to move before the next is pressed, so a slow runner
        # never stacks keys; 4 x 0.5 s stays inside the widened click delay.
        for key in ("Left", "Right", "Up", "Down"):
            press_x11_key(key, process.pid)
            if wait_for(lambda: tree().get("dashboardHighlighted") != f"{ids['click-c']}:left", "",
                        timeout=0.5, required=False):
                break
        assert tree().get("dashboardHighlighted") != f"{ids['click-c']}:left", (
            "no arrow key moved the highlight off the clicked cell")
        assert_nothing_committed("a click superseded by an arrow key", still_open=True)

        # Closing the dashboard inside the delay takes the click with it.
        click("click-c", "Left")
        press_escape(process.pid)
        wait_for(lambda: not tree().get("dashboardMembers"), "Escape did not close the dashboard")
        assert_nothing_committed("a click whose dashboard closed inside its delay", still_open=False)

        # Positive arm: an undisturbed click on a survivor enters exactly that pane once the delay ends.
        open_dashboard()
        click("click-c", "Right")
        wait_for(lambda: not tree().get("dashboardMembers"), "the delayed click never entered its pane",
                 timeout=CLICK_DELAY_MS / 1000 + 10)
        assert active() == "click-c", f"the delayed click selected {active()}, not click-c"
        assert sessions()["click-c"].get("splitFocused"), "the delayed click did not focus the split pane it hit"
        print("OK: delayed dashboard clicks ignore removed members, arrow moves and closes; a clean one enters")
    finally:
        stop(process)
