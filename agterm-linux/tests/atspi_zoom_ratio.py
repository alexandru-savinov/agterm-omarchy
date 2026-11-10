"""Terminal zoom must hand a split back at the ratio it took it at.

Pane extents are measured through each pane's own pty (`stty size`): the grid is what the shell in that pane
really got, which is the extent a user sees.
"""

import contextlib
import os
import shlex
import time

from atspi_smoke import NEGATIVE_SETTLE_SECONDS, control_json, launch, stop, wait_for, window_list, window_tree

RATIO_SLACK = 0.03


def pane_output(env, session_id, pane, command, tag, window_id=None):
    """Run `command` (which writes to `{path}`) in one pane and return what it wrote.

    Retyped because a line typed before the shell reaches its prompt is lost for good; the leading
    newline discards whatever half-line a lost attempt left behind. `pane=None` types to the session's
    default target, which is what a bare control call reaches.
    """
    path = os.path.join(env["AGTERM_STATE_DIR"], f"pane-{tag}")
    line = "\n" + command.format(path=shlex.quote(path)) + "\n"
    arguments = ["session", "type", line, "--target", session_id, "--json"]
    if pane:
        arguments += ["--pane", pane]
    if window_id:
        arguments += ["--window", window_id]

    def written():
        try:
            with open(path, encoding="utf-8") as source:
                return source.read().strip() or None
        except OSError:
            return None

    for _ in range(12):
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        control_json(env, *arguments)
        value = wait_for(written, "", timeout=1, required=False)
        if value:
            return value
    raise AssertionError(f"the {pane or 'default'} pane of session {session_id} never ran {command!r}")


def pane_grid(env, session_id, pane, tag, window_id=None):
    """(rows, cols) the pane's own pty reports."""
    value = pane_output(env, session_id, pane, "stty size > {path}", tag, window_id)
    rows, _, cols = value.partition(" ")
    try:
        return int(rows), int(cols)
    except ValueError as error:
        raise AssertionError(f"`stty size` in the {pane} pane printed {value!r}") from error


def verify_zoom_split_ratio(env):
    process, _ = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        session_id = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]
        samples = iter(range(1000))

        def session():
            return next(item for workspace in window_tree(env, window_id)["workspaces"]
                        for item in workspace["sessions"] if item["id"] == session_id)

        def measured_ratio():
            left = pane_grid(env, session_id, "left", f"left-{next(samples)}", window_id)[1]
            right = pane_grid(env, session_id, "right", f"right-{next(samples)}", window_id)[1]
            return left / (left + right), left, right

        def zoom(slot):
            control_json(env, "surface", "zoom", "show", "--target", f"surface:{session_id}:{slot}",
                         "--window", window_id, "--json")
            canonical = {"primary": "left", "split": "right"}[slot]
            expected = f"surface:{session_id}:{canonical}".lower()
            wait_for(lambda: (window_tree(env, window_id).get("zoomedSurface") or "").lower() == expected,
                     f"the {slot} zoom did not engage")

        def unzoom(slot):
            control_json(env, "surface", "zoom", "hide", "--target", f"surface:{session_id}:{slot}",
                         "--window", window_id, "--json")
            wait_for(lambda: not window_tree(env, window_id).get("zoomedSurface"), "the zoom did not exit")

        def assert_ratio_held(expected, what):
            """Both the model and the two ptys must agree with `expected`, and still agree after a settle,
            so a late divider write cannot pass by landing after the first read."""
            def settled():
                ratio = session().get("splitRatio")
                if ratio is None or abs(ratio - expected) > 0.001:
                    return None
                measured = measured_ratio()
                return measured if abs(measured[0] - expected) <= RATIO_SLACK else None
            if not wait_for(settled, "", timeout=20, required=False):
                raise AssertionError(f"{what}: the model holds {session().get('splitRatio')} and the ptys "
                                     f"measure {measured_ratio()}, expected {expected}")
            time.sleep(NEGATIVE_SETTLE_SECONDS)
            ratio = session().get("splitRatio")
            measured = measured_ratio()
            assert ratio is not None and abs(ratio - expected) <= 0.001, (
                f"{what}: the model ratio drifted to {ratio} after the settle")
            assert abs(measured[0] - expected) <= RATIO_SLACK, (
                f"{what}: the panes drifted to {measured} after the settle")
            return measured

        control_json(env, "session", "split", "on", "--target", session_id, "--window", window_id, "--json")
        wait_for(lambda: session().get("hasSplit"), "the split did not open")
        control_json(env, "session", "resize", "--split-ratio", "0.31", "--target", session_id,
                     "--window", window_id, "--json")
        before = assert_ratio_held(0.31, "before any zoom")
        print(f"measured: split at 0.31 -> {before}")

        # Sidebar visible: the zoom widens the paned, so the divider must be put back after the exit.
        zoom("primary")
        zoom("split")
        unzoom("split")
        after = assert_ratio_held(0.31, "after a primary zoom switched to the split and exited")
        assert abs(after[1] - before[1]) <= 1, (
            f"the primary pane came back at {after[1]} columns, not the {before[1]} it had before the zoom")

        # Sidebar already hidden: the zoom leaves the paned width alone, so the bounded tick wait is the
        # only thing that ends the pending restore.
        control_json(env, "sidebar", "hide", "--json")
        wait_for(lambda: not window_tree(env, window_id).get("sidebarVisible"), "the sidebar did not hide")
        hidden = assert_ratio_held(0.31, "after hiding the sidebar")
        zoom("primary")
        zoom("split")
        unzoom("split")
        assert_ratio_held(0.31, "after a zoom that never changed the paned width")
        zoom("primary")
        unzoom("primary")
        rezoomed = assert_ratio_held(0.31, "after a second zoom with the sidebar hidden")
        assert abs(rezoomed[1] - hidden[1]) <= 1, (
            f"the primary pane came back at {rezoomed[1]} columns, not {hidden[1]}")
        control_json(env, "sidebar", "show", "--json")
        wait_for(lambda: window_tree(env, window_id).get("sidebarVisible"), "the sidebar did not return")

        # Replacement split: the restore queued for the old paned must not be applied to the new host.
        zoom("primary")
        control_json(env, "session", "split", "off", "--target", session_id, "--window", window_id, "--json")
        wait_for(lambda: not session().get("hasSplit"), "the split did not close under zoom")
        control_json(env, "session", "split", "on", "--target", session_id, "--window", window_id, "--json")
        wait_for(lambda: session().get("hasSplit"), "the split did not reopen under zoom")
        replacement = session().get("splitRatio")
        unzoom("primary")
        if replacement is None:
            time.sleep(NEGATIVE_SETTLE_SECONDS)
            assert session().get("splitRatio") is None, (
                f"the replacement split picked up ratio {session().get('splitRatio')} from the zoom exit")
            measured = measured_ratio()
            assert abs(measured[0] - 0.5) <= RATIO_SLACK, (
                f"the replacement split did not open even: {measured}")
        else:
            assert_ratio_held(replacement, "the replacement split after the zoom exit")
        print(f"OK: split ratio 0.31 survives zoom target switches and exits (replacement: {replacement})")
    finally:
        stop(process)
