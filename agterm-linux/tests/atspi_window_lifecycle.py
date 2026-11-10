"""Window-close cancellation, workspace focus filters across removal/restore, and quick-card geometry."""

import json
import os
import time

from atspi_smoke import (
    activate, collect, control_json, describe_tree, keyboard_owner, launch, named, raw_control_json,
    run_palette_action, stop, wait_for, window_extents, window_list, window_tree,
)


def first_window(env):
    return wait_for(lambda: next((item["id"] for item in window_list(env) if item["open"]), None),
                    "initial window was not registered")


def structure(state):
    snapshot = {}
    for folder, _dirs, files in os.walk(state):
        for name in files:
            if name.endswith(".json") and name != "settings.json":
                path = os.path.join(folder, name)
                with open(path, "rb") as file:
                    snapshot[os.path.relpath(path, state)] = file.read()
    return snapshot


def verify_window_close_cancel(env, state):
    for mode in ("default", "live"):
        mode_state = os.path.join(state, f"close-{mode}")
        os.makedirs(os.path.join(mode_state, "windows"))
        if mode == "live":
            with open(os.path.join(mode_state, "settings.json"), "w", encoding="utf-8") as file:
                json.dump({"restoreMode": "live", "closeGraceUndoEnabled": False}, file)
        mode_env = dict(env, AGTERM_STATE_DIR=mode_state, AGTERM_CONTROL_SOCKET=os.path.join(mode_state, "agterm.sock"))
        process, app = launch(mode_env)
        try:
            window = first_window(mode_env)
            control_json(mode_env, "session", "new", "--name", "close-two", "--window", window, "--json")

            def sessions():
                return [row for workspace in window_tree(mode_env, window)["workspaces"] for row in workspace["sessions"]]

            wait_for(lambda: len(sessions()) == 2, "the second session never appeared")
            ids = [row["id"] for row in sessions()]

            def answers(tag):
                markers = {ident: os.path.join(mode_state, f"{tag}-{index}") for index, ident in enumerate(ids)}
                for ident, path in markers.items():
                    control_json(mode_env, "session", "type", f"echo {tag} > {path}\n", "--target", ident, "--json")
                for ident, path in markers.items():
                    wait_for(lambda path=path: os.path.exists(path), f"session {ident} never answered {tag}", timeout=15)

            answers("before")
            time.sleep(1.5)
            saved = structure(mode_state)
            assert saved, "no saved structure to compare against"

            def prompt():
                return named(app, "Quit agterm?")

            control_json(mode_env, "window", "close", window, "--json")
            wait_for(prompt, "closing the last window did not ask to quit")
            activate(wait_for(lambda: named(app, "Cancel", role="push button"), "the quit prompt had no Cancel"))
            wait_for(lambda: prompt() is None, "Cancel did not dismiss the quit prompt")
            assert process.poll() is None, "Cancel quit the app"
            assert next(item for item in window_list(mode_env) if item["id"] == window)["open"], "Cancel closed the window"
            assert [row["id"] for row in sessions()] == ids, f"Cancel changed the sessions: {sessions()}"
            answers("after")
            time.sleep(1.5)
            after = structure(mode_state)
            changed = sorted(name for name in set(saved) | set(after) if saved.get(name) != after.get(name))
            assert not changed, f"a cancelled close rewrote the saved structure: {changed}"

            record = os.path.join(mode_state, "windows", f"{window.lower()}.json")
            if not os.path.exists(record):
                record = next(os.path.join(mode_state, "windows", name) for name in os.listdir(os.path.join(mode_state, "windows"))
                              if name.endswith(".json"))
            control_json(mode_env, "window", "close", window, "--json")
            wait_for(prompt, "the second close did not ask to quit")
            activate(wait_for(lambda: named(app, "Quit", role="push button"), "the quit prompt had no Quit"))
            stamps = []
            deadline = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    stat = os.stat(record)
                    stamp = (stat.st_ino, stat.st_mtime_ns)
                    if not stamps or stamps[-1] != stamp:
                        stamps.append(stamp)
                except FileNotFoundError:
                    pass
                time.sleep(0.01)
            assert process.poll() == 0, f"Quit did not exit cleanly: {process.poll()}"
            flushes = len(stamps) - 1
            assert flushes <= 1, f"Quit flushed the window snapshot {flushes} times"
        finally:
            if process.poll() is None:
                stop(process)
        process, app = launch(mode_env)
        try:
            window = first_window(mode_env)
            restored = [row["id"] for workspace in window_tree(mode_env, window)["workspaces"] for row in workspace["sessions"]]
            assert restored == ids, f"{mode}: the quit snapshot restored {restored}, not {ids}"
        finally:
            stop(process)
        print(f"OK: {mode} mode: Cancel on the last-window quit prompt kept both shells live and the saved structure "
              f"byte-identical; Quit exited 0 after {flushes} observed snapshot flush(es) and restored the same sessions")


def verify_workspace_focus_filters(env):
    process, app = launch(env)
    window = first_window(env)

    def tree():
        return window_tree(env, window)

    def request(cmd, target=None, args=None):
        reply = raw_control_json(env, {"cmd": cmd, "target": target, "args": dict(args or {}, window=window)})
        assert reply["ok"], reply
        return reply.get("result", {})

    def check(step):
        def matches():
            snapshot = tree()
            names = [workspace["name"] for workspace in snapshot["workspaces"]]
            expected = [workspace["name"] for workspace in snapshot["workspaces"]
                        if not snapshot.get("workspaceFilter") or workspace.get("focused")]
            shown = [name for name in names if named(app, name, role="label")]
            active = [workspace["name"] for workspace in snapshot["workspaces"]
                      for row in workspace["sessions"] if row.get("active")]
            return (shown == expected and len(active) == 1 and active[0] in expected), (shown, expected, active)
        wait_for(lambda: matches()[0], f"{step}: sidebar rows/active session disagree with the tree: {matches()[1]}")
        return matches()[1][1]

    try:
        first = tree()["workspaces"][0]["id"]
        request("workspace.rename", first, {"name": "wf-one"})
        two = request("workspace.new", args={"name": "wf-two"})["id"]
        three = request("workspace.new", args={"name": "wf-three"})["id"]
        wait_for(lambda: [item["name"] for item in tree()["workspaces"]] == ["wf-one", "wf-two", "wf-three"],
                 f"the fixture workspaces did not settle: {tree()['workspaces']}")
        check("fixture")

        def select(workspace):
            request("workspace.select", workspace)
            wait_for(lambda: any(row.get("active") for item in tree()["workspaces"] if item["id"] == workspace
                                 for row in item["sessions"]), f"workspace {workspace} never became current")

        select(two)
        run_palette_action(app, process.pid, None, "Focus Workspace")
        assert check("Focus Workspace") == ["wf-two"]
        run_palette_action(app, process.pid, None, "Toggle Workspace Filter")
        assert check("filter off") == ["wf-one", "wf-two", "wf-three"]
        select(three)
        run_palette_action(app, process.pid, None, "Add Workspace to Focus")
        assert check("Add Workspace to Focus") == ["wf-one", "wf-two", "wf-three"]
        run_palette_action(app, process.pid, None, "Toggle Workspace Filter")
        assert check("filter on") == ["wf-two", "wf-three"]
        request("workspace.delete", three)
        wait_for(lambda: all(item["id"] != three for item in tree()["workspaces"]), "wf-three was not removed")
        assert check("member removed") == ["wf-two"]
        focused = [(item["id"], item.get("focused")) for item in tree()["workspaces"]]
        stop(process)
        process, app = launch(env)
        window = first_window(env)
        assert [(item["id"], item.get("focused")) for item in tree()["workspaces"]] == focused, tree()["workspaces"]
        assert tree().get("workspaceFilter"), "the relaunch dropped the workspace filter"
        assert check("relaunch") == ["wf-two"]
        print("OK: Focus Workspace, Add Workspace to Focus and Toggle Workspace Filter keep the sidebar rows and "
              "active session on the tree's focused/workspaceFilter fields through a member removal and a relaunch")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def quick_expected(overlay, header):
    available = overlay.height - header
    width, height = overlay.width * 90 // 100, available * 90 // 100
    return (overlay.width - width) // 2, header + (available - height) // 2, width, height


def verify_quick_card_geometry(env, state):
    settings = os.path.join(state, "settings.json")
    rows = []
    for toolbar in ("normal", "hidden"):
        with open(settings, "w", encoding="utf-8") as file:
            json.dump({"toolbarMode": toolbar}, file)
        process, app = launch(env)
        try:
            window = first_window(env)
            control_json(env, "quick", "show", "--json")
            wait_for(lambda: window_tree(env, window).get("quickVisible"), "the quick terminal did not open")
            previous = None
            for width, height in ((480, 360), (1400, 860)):
                control_json(env, "window", "resize", window, "--width", str(width), "--height", str(height), "--json")

                def measure():
                    panes = [box for node in collect(app, role="split pane") if (box := window_extents(node))]
                    card = window_extents(named(app, "Quick terminal")) if named(app, "Quick terminal") else None
                    if not panes or card is None:
                        return None
                    overlay = max(panes, key=lambda box: box.width * box.height)
                    header = 0
                    toggle = named(app, "Toggle Sidebar (Ctrl+Shift+S)")
                    node = toggle.get_parent() if toggle and toolbar == "normal" else None
                    while node is not None:
                        box = window_extents(node)
                        if box is None or box.height >= 120:
                            break
                        header = box.y + box.height - overlay.y
                        node = node.get_parent()
                    return overlay, header, card

                def settled():
                    first = measure()
                    time.sleep(0.3)
                    second = measure()
                    if not first or not second:
                        return None
                    key = [(box.x, box.y, box.width, box.height) for box in (first[0], first[2])]
                    again = [(box.x, box.y, box.width, box.height) for box in (second[0], second[2])]
                    return second if key == again and second[0].width != previous else None

                overlay, header, card = wait_for(settled, f"the quick card never settled at {width}x{height}", timeout=15)
                previous = overlay.width
                want = quick_expected(overlay, header)
                got = (card.x - overlay.x, card.y - overlay.y, card.width, card.height)
                rows.append((toolbar, f"{overlay.width}x{overlay.height}", header, want, got))
                assert all(abs(a - b) <= 2 for a, b in zip(want, got)), (
                    f"{toolbar} {width}x{height}: card {got} != cardAllocation {want} (overlay {overlay}, header {header})")
                assert got[1] >= header and got[2] > 0 and got[3] > 0, f"{toolbar}: degenerate or overlapping card {got}"
                if toolbar == "hidden":
                    assert header == 0, f"hidden toolbar still measured a {header}px header"
                else:
                    assert 20 <= header < 120, f"normal toolbar measured an implausible {header}px header"
                owner = keyboard_owner(os.path.join(state, f"quick-{toolbar}-{width}"), process.pid)
                assert owner == f"owner=/{window}/", f"typing in the {width}x{height} quick card reached {owner!r}"
        except AssertionError:
            describe_tree(app)
            raise
        finally:
            stop(process)
    print("OK: quick card matches cardAllocation at small and large windows with normal and hidden toolbars: "
          + "; ".join(f"{t} {o} header={h} card={g}" for t, o, h, _w, g in rows))
