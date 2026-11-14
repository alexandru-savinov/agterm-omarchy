"""A zoomed split pane must stay zoomed, and stay the same terminal, when the primary shell exits under it.

The survivor's identity is its PROCESS: `AGTERM_PANE_ID` is baked into the shell at spawn, so a survivor
that answers with the split's id is the original surface, not a recreated one in the primary slot.
"""

import os

from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, actionable, keyboard_owner, launch, named, poll,
                         sidebar_session_row, stop, wait_for, window_list, window_tree, control_json)
from atspi_zoom_ratio import pane_output


def verify_zoom_primary_promotion(env):
    process, app = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        session_id = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "rename", "promotion", "--target", session_id,
                     "--window", window_id, "--json")

        def session():
            return window_tree(env, window_id)["workspaces"][0]["sessions"][0]

        def zoomed():
            return (window_tree(env, window_id).get("zoomedSurface") or "").lower()

        pane_id = 'printf %s "$AGTERM_PANE_ID" > {path}'
        state = env["AGTERM_STATE_DIR"]

        primary_id = pane_output(env, session_id, "left", pane_id, "primary", window_id)
        control_json(env, "session", "split", "on", "--target", session_id, "--window", window_id, "--json")
        wait_for(lambda: session().get("hasSplit"), "the split did not open")
        split_id = pane_output(env, session_id, "right", pane_id, "split", window_id)
        assert split_id != primary_id, "both panes answered with the same pane id"

        control_json(env, "surface", "zoom", "show", "--target", f"surface:{session_id}:split",
                     "--window", window_id, "--json")
        wait_for(lambda: zoomed() == f"surface:{session_id}:right".lower(), "the split zoom did not engage")
        wait_for(lambda: named(app, "Exit Terminal Zoom", role="button"), "the zoom header did not show")

        offset = os.path.getsize(env["AGTERM_UI_APP_STDERR"]) if env.get("AGTERM_UI_APP_STDERR") else None
        # Sent ONCE: after the promotion `--pane left` resolves to the survivor, so a retry would kill it.
        control_json(env, "session", "type", "exit\n", "--target", session_id, "--pane", "left",
                     "--window", window_id, "--json")
        poll(lambda: process.poll() is not None, NEGATIVE_SETTLE_SECONDS)
        assert process.poll() is None, f"the primary exit under a split zoom killed the app (rc={process.returncode})"
        wait_for(lambda: not session().get("hasSplit"), "the primary exit did not collapse the split")

        wait_for(lambda: zoomed() == f"surface:{session_id}:left".lower(),
                 f"the zoom did not follow the promoted survivor; it reads {zoomed()!r}")
        assert named(app, "Exit Terminal Zoom", role="button"), "the zoom header went away with the primary"
        assert sidebar_session_row(app, "promotion") is None, "the sidebar came back while still zoomed"
        survivor = pane_output(env, session_id, "left", pane_id, "promoted", window_id)
        assert survivor == split_id, (
            f"the zoomed primary slot answers as pane {survivor}, not the split survivor {split_id}: "
            "the surface was recreated")
        # `AGTERM_PANE` is baked at spawn too, so the promoted survivor still answers `right`.
        survivor_owner = f"owner={session_id}/{window_id}/right"
        owner = keyboard_owner(os.path.join(state, "zoomed-keyboard"), process.pid)
        assert owner == survivor_owner, f"the zoomed survivor did not take the keyboard (marker read {owner!r})"
        keyed = pane_output(env, session_id, None, pane_id, "zoomed-control", window_id)
        assert keyed == split_id, f"a bare control target reached pane {keyed}, not the survivor {split_id}"

        control_json(env, "surface", "zoom", "hide", "--target", f"surface:{session_id}:primary",
                     "--window", window_id, "--json")
        wait_for(lambda: not zoomed(), "the zoom did not exit")
        wait_for(lambda: sidebar_session_row(app, "promotion"), "the sidebar did not return after the zoom")
        wait_for(lambda: actionable(app, "Toggle Split (Ctrl+Shift+D)"), "the content header did not return")
        assert not named(app, "Exit Terminal Zoom", role="button"), "the zoom header outlived the zoom"
        tree = session()
        assert not tree.get("hasSplit") and not tree.get("splitFocused"), f"the layout did not settle: {tree}"
        owner = keyboard_owner(os.path.join(state, "unzoomed-keyboard"), process.pid)
        assert owner == survivor_owner, f"the keyboard went to {owner!r} after un-zoom"
        keyed = pane_output(env, session_id, None, pane_id, "unzoomed-control", window_id)
        assert keyed == split_id, f"the control target reached pane {keyed} after un-zoom, not {split_id}"

        if offset is not None:
            with open(env["AGTERM_UI_APP_STDERR"], encoding="utf-8", errors="replace") as source:
                source.seek(offset)
                faults = [line.strip() for line in source
                          if "assertion 'GTK_IS_" in line or "GLArea re-realized" in line]
            assert not faults, f"the promotion under zoom damaged the pane tree: {faults[0]}"
        print("OK: a split zoom follows the promoted survivor, keeps its input, and un-zooms cleanly")
    finally:
        stop(process)
