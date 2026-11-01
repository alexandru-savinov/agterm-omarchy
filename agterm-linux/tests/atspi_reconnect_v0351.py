"""Drop actual pane attachments with SSH's failure status, then observe reattachment."""

import contextlib
import os
import signal
import json
from pathlib import Path
import time


def process_status(pid, proc_root="/proc"):
    """Read only process identity/state; never log another process's environment."""
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text()
        # comm may contain spaces/parentheses. Fields after its final ')' start at state (3).
        fields = raw.rsplit(")", 1)[1].split()
        return {"pid": int(pid), "state": fields[0], "ppid": int(fields[1]),
                "start_ticks": fields[19], "exit_code": int(fields[49])}
    except (OSError, IndexError, ValueError):
        return {"pid": int(pid), "unavailable": True}


class ReconnectDiagnostics:
    """Retain transitions rather than identical polls, without changing the wait predicate."""

    def __init__(self):
        self.started = time.monotonic()
        self.snapshots = []
        self.signalled = []
        self.last = None

    def observe(self, session):
        rows = [{key: row[key] for key in ("kind", "paneID", "reconnect", "lead") if key in row}
                for row in session["surfaces"] if row["kind"] in ("left", "right")]
        if rows != self.last:
            self.snapshots.append({"elapsed": round(time.monotonic() - self.started, 3), "panes": rows})
            self.snapshots = self.snapshots[-128:]
            self.last = rows
        return session

    def report(self, phase, ssh_down):
        events = Path(ssh_down + ".processes.log")
        try:
            with events.open("rb") as source:
                source.seek(max(0, events.stat().st_size - 65536))
                fixture_events = source.read().decode(errors="replace")
        except OSError as error:
            fixture_events = type(error).__name__
        return {"phase": phase, "elapsed": round(time.monotonic() - self.started, 3),
                "ssh_down": os.path.exists(ssh_down), "snapshots": self.snapshots,
                "signalled": self.signalled,
                "processes_after": [process_status(row["pid"]) for row in self.signalled],
                "fixture_events": fixture_events}


def verify_pane_reconnect(viewer_session, ssh, ssh_down, origin_state):
    from atspi_smoke import wait_for

    diagnostics = ReconnectDiagnostics()
    read_session = viewer_session
    viewer_session = lambda: diagnostics.observe(read_session())
    panes = {row["kind"]: row["paneID"] for row in viewer_session()["surfaces"]
             if row["kind"] in ("left", "right")}
    marker = ("AGTERM_TEST_ORIGIN_STATE=" + origin_state).encode()
    with open(ssh_down, "w", encoding="utf-8"):
        pass
    dropped = 0
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as source:
                argv = source.read().split(b"\0")
            with open(f"/proc/{pid}/environ", "rb") as source:
                environment = source.read().split(b"\0")
        except OSError:
            continue
        if ssh.encode() in argv and any(b"'attach'" in argument for argument in argv) and marker in environment:
            with contextlib.suppress(ProcessLookupError):
                before = process_status(pid)
                os.kill(int(pid), signal.SIGTERM)
                diagnostics.signalled.append(before)
                dropped += 1
    assert dropped == len(panes), f"dropped {dropped} pane attachments for {panes}"

    def waiting():
        rows = {row["kind"]: row for row in viewer_session()["surfaces"] if row["kind"] in panes}
        return len(rows) == len(panes) and all(
            row.get("reconnect", {}).get("failures", 0) > 0
            and "Connection refused" in row.get("reconnect", {}).get("reason", "") for row in rows.values())

    try:
        wait_for(waiting, "pane link loss did not publish failed-probe reason/count", timeout=25)
    except AssertionError:
        print("RECONNECT_DIAGNOSTICS " + json.dumps(diagnostics.report("failed-probe", ssh_down), sort_keys=True),
              flush=True)
        raise
    assert {row["kind"]: row["paneID"] for row in viewer_session()["surfaces"]
            if row["kind"] in panes} == panes, "link loss changed pane identity"
    os.remove(ssh_down)

    def reattached():
        rows = {row["kind"]: row for row in viewer_session()["surfaces"] if row["kind"] in panes}
        return len(rows) == len(panes) and all(not row.get("reconnect") and row.get("lead") in ("leader", "follower")
                                              for row in rows.values())

    if not wait_for(reattached, "pane did not reconnect after its host recovered", timeout=30, required=False):
        print("RECONNECT_DIAGNOSTICS " + json.dumps(diagnostics.report("recovery", ssh_down), sort_keys=True),
              flush=True)
        raise AssertionError(viewer_session())
    assert {row["kind"]: row["paneID"] for row in viewer_session()["surfaces"]
            if row["kind"] in panes} == panes, "reattachment changed pane identity"
    print("OK: SSH-255 pane loss published retry reason/count and reattached with stable pane identities")
