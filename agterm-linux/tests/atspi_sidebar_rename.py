"""Inline sidebar renames against same-row updates, row moves and removals over the control socket."""

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

from atspi_smoke import (  # noqa: E402
    collect, control_json, describe_tree, editable_descendant, launch, named, press_escape, press_return,
    raw_control_json, run_palette_action, sidebar_accessible_live, sidebar_session_row, stop, type_x11_text,
    wait_for, window_list, window_tree,
)


def entry_state(entry):
    text = Atspi.Text.get_text(entry, 0, Atspi.Text.get_character_count(entry))
    return text, entry.get_text_iface().get_caret_offset()


def verify_sidebar_rename_sync(env):
    process, app = launch(env)
    try:
        window_id = wait_for(lambda: next((item["id"] for item in window_list(env) if item["open"]), None),
                             "initial window was not registered")

        def tree():
            return window_tree(env, window_id)

        def sessions():
            return [session for workspace in tree()["workspaces"] for session in workspace["sessions"]]

        def session(ident):
            return next((item for item in sessions() if item["id"] == ident), None)

        def names():
            return [item["name"] for item in sessions()]

        def sidebar_entries():
            found = []
            for item in collect(app):
                try:
                    if item.get_editable_text_iface():
                        found.append(item)
                except Exception:
                    pass
            return found

        first = wait_for(lambda: (sessions() or [None])[0], "the launched session never reported")["id"]
        control_json(env, "session", "rename", "ren-one", "--target", first, "--json")
        ident = {"one": first}
        for key in ("two", "three", "doomed"):
            ident[key] = control_json(env, "session", "new", "--name", f"ren-{key}", "--window", window_id,
                                      "--json")["result"]["id"]
        wait_for(lambda: names() == ["ren-one", "ren-two", "ren-three", "ren-doomed"],
                 f"the fixture sessions did not settle: {names()}")
        for key in ident:
            wait_for(lambda key=key: sidebar_session_row(app, f"ren-{key}"), f"the ren-{key} row never appeared")
        assert not sidebar_entries(), "an editable accessible exists before any rename, so the leftover checks are blind"

        def begin_session_rename(key):
            control_json(env, "session", "select", "--target", ident[key], "--json")
            wait_for(lambda: session(ident[key])["active"], f"ren-{key} never became active")
            row = wait_for(lambda: sidebar_session_row(app, session(ident[key])["name"]), f"the ren-{key} row is missing")
            run_palette_action(app, process.pid, None, "Rename Session")
            return row, wait_for(lambda: editable_descendant(row), f"Rename Session opened no entry in ren-{key}")

        def assert_not_stuck(key, step):
            wait_for(lambda: not sidebar_entries(), f"{step} left an inline rename entry in the sidebar")
            row, _entry = begin_session_rename(key)
            press_escape(process.pid)
            wait_for(lambda: editable_descendant(row) is None, f"after {step}, Escape did not close a new rename")

        row, entry = begin_session_rename("two")
        type_x11_text("ren-pa", process.pid)
        wait_for(lambda: entry_state(entry) == ("ren-pa", 6), f"partial rename text never landed: {entry_state(entry)}")
        control_json(env, "session", "status", "blocked", "--target", ident["two"], "--json")
        control_json(env, "session", "flag", "on", "--target", ident["two"], "--json")
        wait_for(lambda: session(ident["two"]).get("status") == "blocked" and session(ident["two"]).get("flagged"),
                 "the same-row status and flag updates never reached the model")
        wait_for(lambda: len(collect(row, role="image")) >= 2, "the flag star never reached the renaming row")
        assert sidebar_accessible_live(entry), "a same-row status/flag update destroyed the rename entry"
        assert entry_state(entry) == ("ren-pa", 6), (
            f"a same-row status/flag update moved the rename entry's text or caret: {entry_state(entry)}")
        type_x11_text("rtial", process.pid)
        press_return(process.pid)
        wait_for(lambda: session(ident["two"])["name"] == "ren-partial",
                 f"the partially typed rename did not commit intact: {names()}")

        before = {key: session(ident[key])["name"] for key in ident}
        row, entry = begin_session_rename("three")
        type_x11_text("ren-moved", process.pid)
        wait_for(lambda: entry_state(entry)[0] == "ren-moved", "the move-leg rename text never landed")
        control_json(env, "session", "move", "--to", "top", "--target", ident["three"], "--json")
        wait_for(lambda: names()[0] in ("ren-moved", "ren-three"), f"the renaming row never moved: {names()}")
        wait_for(lambda: not sidebar_entries(), "moving the renaming row left its entry behind")
        moved = session(ident["three"])["name"]
        assert moved in ("ren-moved", "ren-three"), f"the moved row's rename resolved to {moved!r}"
        assert {key: session(ident[key])["name"] for key in ident if key != "three"} == {
            key: name for key, name in before.items() if key != "three"}, f"a row move renamed another row: {names()}"
        move_outcome = "commit" if moved == "ren-moved" else "cancel"
        assert_not_stuck("one", "a row move during a rename")

        row, entry = begin_session_rename("doomed")
        type_x11_text("ren-ghost", process.pid)
        wait_for(lambda: entry_state(entry)[0] == "ren-ghost", "the remove-leg rename text never landed")
        control_json(env, "session", "close", "--target", ident["doomed"], "--json")
        wait_for(lambda: session(ident["doomed"]) is None, "the renaming session was not removed")
        wait_for(lambda: sidebar_session_row(app, "ren-doomed") is None and sidebar_session_row(app, "ren-ghost") is None,
                 "the removed row stayed in the sidebar")
        assert "ren-ghost" not in names(), f"the removed row's rename landed on another session: {names()}"
        assert_not_stuck("one", "removing the renaming row")

        workspace = tree()["workspaces"][0]
        header = wait_for(lambda: named(app, workspace["name"], role="label"), "the workspace header never appeared")
        header = header.get_parent()
        control_json(env, "session", "select", "--target", ident["one"], "--json")
        run_palette_action(app, process.pid, None, "Rename Workspace")
        entry = wait_for(lambda: editable_descendant(header), "Rename Workspace opened no header entry")
        type_x11_text("ws-pa", process.pid)
        wait_for(lambda: entry_state(entry) == ("ws-pa", 5), f"partial header text never landed: {entry_state(entry)}")
        control_json(env, "session", "status", "active", "--target", ident["one"], "--json")
        control_json(env, "notify", "--title", "rename", "header", "--target", ident["two"],
                     "--window", window_id, "--json")
        wait_for(lambda: session(ident["one"]).get("status") == "active" and session(ident["two"]).get("unseen", 0) > 0,
                 "the workspace's row updates never reached the model")
        assert sidebar_accessible_live(entry), "a row update in the workspace destroyed the header rename entry"
        assert entry_state(entry) == ("ws-pa", 5), (
            f"a row update in the workspace moved the header entry's text or caret: {entry_state(entry)}")
        type_x11_text("rt", process.pid)
        press_return(process.pid)
        wait_for(lambda: tree()["workspaces"][0]["name"] == "ws-part", "the partial header rename did not commit intact")

        doomed = raw_control_json(env, {"cmd": "workspace.new", "args": {"name": "ws-doomed", "window": window_id}})
        assert doomed["ok"], doomed
        doomed_id = doomed["result"]["id"]
        wait_for(lambda: named(app, "ws-doomed", role="label"), "the second workspace header never appeared")
        doomed_header = named(app, "ws-doomed", role="label").get_parent()
        raw_control_json(env, {"cmd": "workspace.select", "target": doomed_id, "args": {"window": window_id}})
        run_palette_action(app, process.pid, None, "Rename Workspace")
        entry = wait_for(lambda: editable_descendant(doomed_header), "Rename Workspace opened no entry on ws-doomed")
        type_x11_text("ws-ghost", process.pid)
        wait_for(lambda: entry_state(entry)[0] == "ws-ghost", "the header remove-leg text never landed")
        deleted = raw_control_json(env, {"cmd": "workspace.delete", "target": doomed_id, "args": {"window": window_id}})
        assert deleted["ok"], deleted
        wait_for(lambda: all(item["id"] != doomed_id for item in tree()["workspaces"]),
                 "the renaming workspace was not removed")
        assert [item["name"] for item in tree()["workspaces"]] == ["ws-part"], (
            f"the removed header's rename landed elsewhere: {tree()['workspaces']}")
        assert_not_stuck("one", "removing the renaming workspace")

        print("OK: inline renames keep text and caret across same-row and same-workspace updates, and a row "
              f"move ({move_outcome}), row removal and workspace removal each resolve the rename once")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)
