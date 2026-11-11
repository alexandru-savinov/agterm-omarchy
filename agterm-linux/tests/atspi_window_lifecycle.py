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
    import ctypes
    import select
    import shlex
    import signal
    import struct
    import sys

    class Publications:
        """Watch the directory before an action; replaced inode polling loses rapid publications."""
        def __init__(self, folder):
            libc = ctypes.CDLL(None, use_errno=True)
            libc.inotify_init1.argtypes = [ctypes.c_int]
            libc.inotify_init1.restype = ctypes.c_int
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            libc.inotify_add_watch.restype = ctypes.c_int
            self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            assert self.fd >= 0, f"inotify_init1: {ctypes.get_errno()}"
            # Include temporary-file moves so adjacent publications cannot be coalesced.
            self.watch = libc.inotify_add_watch(self.fd, os.fsencode(folder), 0xC0 | 0x400 | 0x800)
            if self.watch < 0:
                os.close(self.fd)
                raise AssertionError(f"inotify_add_watch: {ctypes.get_errno()}")

        def drain(self):
            events = []
            while select.select([self.fd], [], [], 0)[0]:
                data = os.read(self.fd, 65536)
                offset = 0
                while offset < len(data):
                    assert len(data) - offset >= 16, "truncated inotify header"
                    watch, mask, cookie, length = struct.unpack_from("iIII", data, offset)
                    offset += 16
                    assert offset + length <= len(data), "truncated inotify name"
                    name = os.fsdecode(data[offset:offset + length].split(b"\0", 1)[0])
                    offset += length
                    assert not mask & (0x4000 | 0x8000 | 0x400 | 0x800), f"lost publication watch: {mask:#x}"
                    assert watch == self.watch, f"unexpected watch {watch}"
                    if mask & 0x80:
                        assert cookie, "atomic publication had no rename cookie"
                        events.append((name, cookie))
            return events

        def close(self):
            os.close(self.fd)

    # Prove this observer distinguishes rapid replacements before trusting a single-publication result.
    watch_dir = os.path.join(state, "publication-self-check")
    os.makedirs(watch_dir)
    check = Publications(watch_dir)
    try:
        for index in range(3):
            temporary = os.path.join(watch_dir, f"temporary-{index}")
            with open(temporary, "w", encoding="utf-8") as file:
                file.write(str(index))
            os.replace(temporary, os.path.join(watch_dir, "window.json"))
        observed = check.drain()
        assert len(observed) == len(set(observed)) == 3, observed
        assert all(name == "window.json" for name, _ in observed), observed
    finally:
        check.close()

    # A real foreground program gives Live identity and Re-run/Fresh behavior distinct oracles.
    job = os.path.join(state, "quit-job.py")
    with open(job, "w", encoding="utf-8") as file:
        file.write("import os, signal, sys\n"
                   "def respond(*_):\n    with open(sys.argv[1] + '.reply', 'a') as f: f.write(str(os.getpid()) + '\\n')\n"
                   "signal.signal(signal.SIGUSR1, respond)\n"
                   "with open(sys.argv[1], 'a') as f: f.write(str(os.getpid()) + '\\n')\n"
                   "while True: signal.pause()\n")

    for mode in ("none", "rerun", "live"):
        for exit_kind in ("accept", "SIGTERM", "SIGINT"):
            mode_state = os.path.join(state, f"close-{mode}-{exit_kind}")
            windows = os.path.join(mode_state, "windows")
            os.makedirs(windows)
            with open(os.path.join(mode_state, "settings.json"), "w", encoding="utf-8") as file:
                json.dump({"restoreMode": mode, "closeGraceUndoEnabled": False}, file)
            mode_env = dict(env, AGTERM_STATE_DIR=mode_state,
                            AGTERM_CONTROL_SOCKET=os.path.join(mode_state, "agterm.sock"))
            process, app = launch(mode_env)
            observer = None
            job_fd = None
            ids = []
            try:
                window = first_window(mode_env)
                control_json(mode_env, "session", "new", "--name", "close-two", "--window", window, "--json")

                def sessions():
                    return [row for workspace in window_tree(mode_env, window)["workspaces"]
                            for row in workspace["sessions"]]

                wait_for(lambda: len(sessions()) == 2, "the second session never appeared")
                ids = [row["id"] for row in sessions()]

                def answers(tag, targets=None):
                    markers = {ident: os.path.join(mode_state, f"{tag}-{index}")
                               for index, ident in enumerate(targets or ids)}
                    for ident, path in markers.items():
                        control_json(mode_env, "session", "type", f"echo {tag} > {shlex.quote(path)}\n",
                                     "--target", ident, "--json")
                    for ident, path in markers.items():
                        wait_for(lambda path=path: os.path.exists(path),
                                 f"session {ident} never answered {tag}", timeout=15)

                answers("before")
                time.sleep(1.5)
                saved = structure(mode_state)
                assert saved, "no saved structure to compare against"
                records = [name for name in os.listdir(windows) if name.casefold() == f"{window.lower()}.json"]
                assert len(records) == 1, f"missing or ambiguous record for window {window}: {records}"
                record = records[0]  # Foundation's UUID filename is uppercase; the wire ID may not be.
                observer = Publications(windows)

                def prompt():
                    return named(app, "Quit agterm?")

                control_json(mode_env, "window", "close", window, "--json")
                wait_for(prompt, "closing the last window did not ask to quit")
                activate(wait_for(lambda: named(app, "Cancel", role="button"), "the quit prompt had no Cancel"))
                wait_for(lambda: prompt() is None, "Cancel did not dismiss the quit prompt")
                assert process.poll() is None, "Cancel quit the app"
                assert next(item for item in window_list(mode_env) if item["id"] == window)["open"]
                assert [row["id"] for row in sessions()] == ids, f"Cancel changed sessions: {sessions()}"
                answers("after")
                time.sleep(1.5)
                assert structure(mode_state) == saved, "Cancel changed saved structure"
                assert not observer.drain(), "Cancel published a window snapshot"

                # A non-final close must still publish its own record and leave this window usable.
                other = control_json(mode_env, "window", "new", "ordinary-close", "--json")["result"]["id"]
                wait_for(lambda: any(item["id"] == other and item["open"] for item in window_list(mode_env)),
                         "ordinary-close window did not open")
                time.sleep(1.5)
                observer.drain()
                control_json(mode_env, "window", "close", other, "--json")
                wait_for(lambda: all(item["id"] != other or not item["open"] for item in window_list(mode_env)),
                         "ordinary close did not close its window")
                events = observer.drain()
                assert sum(name.casefold() == f"{other.lower()}.json" for name, _ in events) == 1, events
                assert not any(name == record for name, _ in events), events
                answers("ordinary-close-survivor")

                pids = os.path.join(mode_state, "job-pids")
                control_json(mode_env, "session", "type",
                             f"{shlex.quote(sys.executable)} {shlex.quote(job)} {shlex.quote(pids)}\n",
                             "--target", ids[0], "--json")

                def jobs():
                    try:
                        with open(pids, encoding="utf-8") as file:
                            return [int(line) for line in file if line.strip()]
                    except FileNotFoundError:
                        return []

                wait_for(jobs, "foreground marker job did not start")
                original_pid = jobs()[0]
                job_fd = os.pidfd_open(original_pid)
                with open(f"/proc/{original_pid}/stat", encoding="utf-8") as file:
                    birth = file.read().rsplit(")", 1)[1].split()[19]
                time.sleep(1.5)
                if exit_kind == "accept":
                    control_json(mode_env, "window", "close", window, "--json")
                    wait_for(prompt, "the second close did not ask to quit")
                observer.drain()
                if exit_kind == "accept":
                    activate(wait_for(lambda: named(app, "Quit", role="button"), "the quit prompt had no Quit"))
                else:
                    process.send_signal(getattr(signal, exit_kind))
                events = []
                deadline = time.monotonic() + 20
                while process.poll() is None and time.monotonic() < deadline:
                    events.extend(observer.drain())
                    select.select([observer.fd], [], [], 0.05)
                events.extend(observer.drain())
                assert process.poll() == 0, f"{exit_kind} did not exit cleanly: {process.poll()}"
                publications = [(name, cookie) for name, cookie in events if name == record]
                assert len(publications) == 1, f"{mode}/{exit_kind}: expected one final publication, got {publications}"
                assert len(set(publications)) == len(publications), "duplicate rename identity"
                observer.close()
                observer = None

                process, app = launch(mode_env)
                window = first_window(mode_env)
                restored = [row["id"] for row in sessions()]
                assert restored == ids, f"{mode}: restored {restored}, not {ids}"
                answers("restored-shell", [ids[1]])
                if mode == "rerun":
                    # Re-run deliberately pre-fills the captured command without executing it.
                    wait_for(lambda: select.select([job_fd], [], [], 0)[0], "Re-run left the old job running")
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        assert [row["id"] for row in sessions()] == ids
                        assert jobs() == [original_pid], "Re-run executed before explicit Enter"
                        time.sleep(0.05)
                    control_json(mode_env, "session", "type", "\n", "--target", ids[0], "--json")
                    wait_for(lambda: len(jobs()) == 2, "Enter did not run the pre-filled captured command")
                    assert jobs()[1] != original_pid, "Re-run retained the old process"
                elif mode == "live":
                    assert jobs() == [original_pid] and not select.select([job_fd], [], [], 0)[0], (
                        "Live did not preserve the foreground program identity")
                    with open(f"/proc/{original_pid}/stat", encoding="utf-8") as file:
                        assert file.read().rsplit(")", 1)[1].split()[19] == birth, "Live PID birth changed"
                    signal.pidfd_send_signal(job_fd, signal.SIGUSR1)
                    wait_for(lambda: os.path.exists(pids + ".reply") and
                             open(pids + ".reply", encoding="utf-8").read().strip() == str(original_pid),
                             "the preserved Live program did not respond after reopen")
                else:
                    answers("fresh-shell", [ids[0]])
                    assert jobs() == [original_pid], "Fresh re-ran the foreground program"
                if mode != "live":
                    wait_for(lambda: select.select([job_fd], [], [], 0)[0],
                             f"{mode} left the old foreground job running")
                # End only this fixture's panes/daemons, including the preserved Live program.
                for ident in ids:
                    control_json(mode_env, "session", "close", "--target", ident, "--json")
                print(f"OK: {mode}/{exit_kind}: Cancel preserved shells and bytes, ordinary close saved once, "
                      "final shutdown published one snapshot, and restore behavior matched the mode")
            finally:
                if observer is not None:
                    observer.close()
                try:
                    if mode == "live" and ids:
                        # Cleanup through the existing identity-scoped host API, even after a failed
                        # post-exit assertion. Never sweep unrelated daemons or signal a bare PID.
                        if process.poll() is not None:
                            process, app = launch(mode_env)
                        for ident in ids:
                            reply = raw_control_json(mode_env, {"cmd": "zmx.kill", "target": ident,
                                                     "args": {"window": window, "pane": "left", "force": True}})
                            if not reply.get("ok"):
                                assert ("no left pane daemon" in reply.get("error", "") or
                                        "no such window" in reply.get("error", "")), reply
                finally:
                    try:
                        if job_fd is not None:
                            try:
                                if not select.select([job_fd], [], [], 0)[0]:
                                    try:
                                        signal.pidfd_send_signal(job_fd, signal.SIGTERM)
                                    except ProcessLookupError:
                                        pass  # The exact process exited between poll and signal.
                            finally:
                                os.close(job_fd)
                    finally:
                        if process.poll() is None:
                            stop(process)


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
        for workspace in (two, three):
            # placed by id: selecting a still-empty workspace has no session to make current, so an
            # unplaced session.new lands in whichever workspace is current (wf-three, the last created)
            request("session.new", args={"name": f"wf-session-{workspace[:4]}", "workspace": workspace})
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
                    panes = [box for node in collect(app) if node.get_role_name() not in ("frame", "window", "application")
                             and (box := window_extents(node))]
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
