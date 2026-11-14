"""In-terminal search focus across sidebar structural reconciliation (#164).

A live search (Ctrl+Shift+F) owns the keyboard outside the terminal (`Search.swift`), so a sidebar sync
that inserts, moves or removes rows must leave it there, and a context menu opened over it must hand the
keyboard back to the ENTRY when it goes down — by Escape, or inside `syncSidebar()` when the row hosting it
is detached (`AppControllerSidebarSync.finishSidebarSync`).

Every leg is judged on observed app behaviour, never on a harness flag:
  * keyboard owner: a character typed through X11 must reach the QUERY (the match label flips to
    "No results"); typed into the shell instead, the count just sits where it was;
  * selection: the tree's `active` session AND the row's published STATE_SELECTED (re-published one main
    loop turn after a re-root), with every other row present and deselected;
  * responsiveness: a control `tree` round trip answered inside a bound after each reconciliation.
The focused accessible (role:name) is recorded per leg as context; it is not the verdict, because a11y
FOCUSED is unreliable while a popover deactivates the toplevel.

All legs run and print a RECORD line before the scenario fails on the collected verdicts, so one CI run
shows the whole matrix.
"""

import time

from atspi_smoke import (
    ABSOLUTE_DYS, ChromeFocus, Atspi, actionable, calibrate_row_click, collect, control_json,
    describe_tree, named, poll, press_escape, press_x11_key, right_click, row_deselected, row_selected,
    sidebar_row_name_order, sidebar_session_row, sidebar_session_row_label, stop, type_x11_text, wait_for,
)

SEARCH_NEXT = "Next match (Enter)"
# A starved main loop (the popover-in-a-disposed-list-box shape) never answers; a healthy one answers in
# well under a second even on the slow aarch64 runner.
CONTROL_RTT_LIMIT_SECONDS = 5.0


def focused_accessible(app):
    """role:name of the deepest accessible reporting FOCUSED, or None."""
    found = None
    for item in collect(app):
        try:
            if item.get_state_set().contains(Atspi.StateType.FOCUSED):
                found = f"{item.get_role_name()}:{item.get_name() or ''}"
        except Exception:
            continue
    return found


def verify_sidebar_search_focus(env):
    ctx = ChromeFocus(env)
    failures = []
    try:
        ctx.setup()
        pid = ctx.process.pid
        app = ctx.app
        origin_id = ctx.session_id
        origin = next(s["name"] for s in ctx.sessions() if s["id"] == origin_id)

        def names():
            return [s["name"] for s in ctx.sessions()]

        ident = {}
        for name in ("sf-b", "sf-c"):
            ident[name] = control_json(env, "session", "new", "--name", name, "--no-select",
                                       "--json")["result"]["id"]
        wait_for(lambda: names() == [origin, "sf-b", "sf-c"], "the fixture sessions did not settle")
        for name in (origin, "sf-b", "sf-c"):
            wait_for(lambda name=name: sidebar_session_row(app, name), f"the {name} row never appeared")
        # Calibrate the pointer offset on a NON-selected row (calibration selects its target), before any
        # search is open, then put the selection back.
        row_dy = calibrate_row_click(app, pid, "sf-b")
        ctx.select_session(origin_id, "the origin session did not become active again")
        wait_for(lambda: row_selected(app, origin), "the origin row never published SELECTED")
        control_json(env, "session", "type", "--target", origin_id, "echo zzq zzq zzq\n", "--json")
        ctx.open_counted_query("for the structural-reconciliation legs")

        def typing_reaches_query():
            """Positive keyboard-owner probe that leaves the query as it found it."""
            type_x11_text("x", pid)
            reached = poll(lambda: named(app, "No results", role="label") is not None, timeout=4)
            if reached:
                press_x11_key("BackSpace", pid)
                wait_for(ctx.match_count, "BackSpace did not restore the counted query")
            return reached

        def recover(step):
            """The keyboard went to the shell: clear its line, cycle the search, re-count the query."""
            press_x11_key("ctrl+c", pid)
            if actionable(app, SEARCH_NEXT):
                ctx.close_search(f"while recovering from {step}")
            ctx.open_counted_query(f"while recovering from {step}")

        def observe(step, expect_order=None):
            started = time.monotonic()
            tree_sessions = ctx.sessions()
            rtt = time.monotonic() - started
            active = next((s["id"] for s in tree_sessions if s.get("active")), None)
            others = [s["name"] for s in tree_sessions if s["id"] != origin_id]
            record = {
                "search_open": actionable(app, SEARCH_NEXT) is not None,
                "active_is_origin": active == origin_id,
                "origin_row_selected": bool(wait_for(lambda: row_selected(app, origin),
                                                     "", timeout=6, required=False)),
                "others_deselected": all(row_deselected(app, name) for name in others),
                "focused": focused_accessible(app),
                "control_rtt_ms": round(rtt * 1000),
            }
            if expect_order is not None:
                record["order"] = sidebar_row_name_order(app)
            record["typing_reaches_query"] = record["search_open"] and typing_reaches_query()
            print(f"RECORD {step}: {record}")
            bad = [key for key in ("search_open", "active_is_origin", "origin_row_selected",
                                   "others_deselected", "typing_reaches_query") if not record[key]]
            if rtt > CONTROL_RTT_LIMIT_SECONDS:
                bad.append(f"control_rtt {record['control_rtt_ms']}ms")
            if expect_order is not None and record["order"] != expect_order:
                bad.append(f"order {record['order']} != {expect_order}")
            if ctx.process.poll() is not None:
                bad.append("the app exited")
            if bad:
                failures.append(f"{step}: {', '.join(bad)}")
                if ctx.process.poll() is None and not record["typing_reaches_query"]:
                    recover(step)

        observe("baseline")

        # INSERT: a background session lands as a new row without touching the selection.
        ident["sf-ins"] = control_json(env, "session", "new", "--name", "sf-ins", "--no-select",
                                       "--json")["result"]["id"]
        wait_for(lambda: sidebar_session_row(app, "sf-ins"), "the inserted row never appeared")
        observe("insert-row", [origin, "sf-b", "sf-c", "sf-ins"])

        # MOVE another row: a remove + insert of that row only.
        control_json(env, "session", "move", "--to", "top", "--target", ident["sf-ins"], "--json")
        wait_for(lambda: sidebar_row_name_order(app)[:1] == ["sf-ins"], "the moved row never reached the top")
        observe("move-other-row", ["sf-ins", origin, "sf-b", "sf-c"])

        # MOVE the selected row: GTK re-roots it and resets SELECTED, which must be re-published.
        control_json(env, "session", "move", "--to", "bottom", "--target", origin_id, "--json")
        wait_for(lambda: sidebar_row_name_order(app)[-1:] == [origin],
                 "the selected row never reached the bottom")
        observe("move-selected-row", ["sf-ins", "sf-b", "sf-c", origin])

        # REMOVE another row.
        control_json(env, "session", "close", "--target", ident["sf-ins"], "--json")
        wait_for(lambda: sidebar_session_row(app, "sf-ins") is None, "the closed row never left the sidebar")
        observe("remove-row", ["sf-b", "sf-c", origin])

        # UNRELATED in-place updates on other rows.
        control_json(env, "session", "status", "active", "--target", ident["sf-b"], "--json")
        wait_for(lambda: any(s["id"] == ident["sf-b"] and s.get("status") == "active"
                             for s in ctx.sessions()),
                 "session.status never reached the sf-b session")
        control_json(env, "session", "rename", "sf-c2", "--target", ident["sf-c"], "--json")
        wait_for(lambda: sidebar_session_row(app, "sf-c2"), "the rename never reached the sidebar")
        observe("unrelated-updates", ["sf-b", "sf-c2", origin])
        control_json(env, "session", "status", "idle", "--target", ident["sf-b"], "--json")

        def open_menu_on_origin(step):
            """Right-click the SELECTED row: no selection change, so the search stays live under the menu."""
            for dy in (row_dy, *(d for d in ABSOLUTE_DYS if d != row_dy)):
                right_click(lambda: sidebar_session_row_label(app, origin), pid, dy=dy)
                if poll(lambda: actionable(app, "Flag") is not None, timeout=2):
                    if (next((s["id"] for s in ctx.sessions() if s.get("active")), None) == origin_id
                            and actionable(app, SEARCH_NEXT)):
                        return
                    # The probe hit another row: that selected it and ended the search. Undo and retry.
                    press_escape(pid)
                    wait_for(lambda: actionable(app, "Flag") is None, "a stray context menu never closed")
                    ctx.select_session(origin_id, "the origin session did not become active again")
                    if not actionable(app, SEARCH_NEXT):
                        ctx.open_counted_query(f"after a stray right-click during {step}")
            raise AssertionError(
                f"no right-click opened the origin row's context menu over the search ({step})")

        # Intended dismissal by GTK (Escape): `contextMenuDidClose` restores the captured entry.
        open_menu_on_origin("escape-dismissal")
        press_escape(pid)
        wait_for(lambda: actionable(app, "Flag") is None, "Escape did not dismiss the context menu")
        observe("menu-escape-dismissal")

        # Dismissal INSIDE the sync: collapsing the workspace detaches the list box hosting the menu, so
        # `detachGuard` downs it with `refocus: false` and `finishSidebarSync` must restore the entry.
        # `workspace collapse` syncs the sidebar only — no `showActive` grab follows to mask the tail.
        open_menu_on_origin("in-sync-dismissal")
        workspace_id = ctx.workspace()["id"]
        control_json(env, "workspace", "collapse", "--target", workspace_id, "--json")
        wait_for(lambda: actionable(app, "Flag") is None,
                 "collapsing the workspace did not take down the context menu parented inside it")
        wait_for(lambda: sidebar_row_name_order(app) == [], "the collapse did not hide the rows")
        started = time.monotonic()
        collapsed = ctx.workspace().get("collapsed")
        rtt = time.monotonic() - started
        search_open = actionable(app, SEARCH_NEXT) is not None
        reached = search_open and typing_reaches_query()
        print(f"RECORD menu-in-sync-dismissal: {{'search_open': {search_open}, 'collapsed': {collapsed}, "
              f"'focused': {focused_accessible(app)!r}, 'control_rtt_ms': {round(rtt * 1000)}, "
              f"'typing_reaches_query': {reached}}}")
        if not (search_open and collapsed and reached and rtt <= CONTROL_RTT_LIMIT_SECONDS):
            failures.append(f"menu-in-sync-dismissal: search_open={search_open} collapsed={collapsed} "
                            f"typing_reaches_query={reached} rtt={round(rtt * 1000)}ms")
            if not reached:
                recover("menu-in-sync-dismissal")
        control_json(env, "workspace", "expand", "--target", workspace_id, "--json")
        wait_for(lambda: sidebar_row_name_order(app) == ["sf-b", "sf-c2", origin],
                 "expanding the workspace did not bring the rows back")
        observe("after-expand", ["sf-b", "sf-c2", origin])

        assert not failures, "search focus did not survive sidebar reconciliation: " + "; ".join(failures)
        print("OK: a live search keeps the keyboard and selection through sidebar inserts, moves, removals "
              "and unrelated updates, and context-menu dismissals hand it back to the entry")
    except AssertionError:
        describe_tree(ctx.app)
        raise
    finally:
        stop(ctx.process)
