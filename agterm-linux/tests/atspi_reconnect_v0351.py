"""Drop actual pane attachments with SSH's failure status, then observe reattachment."""

import contextlib
import os
import signal
import json
from pathlib import Path
import re
import stat
import time
import uuid


def process_status(pid, proc_root="/proc", expected_start=None):
    """Read only process identity/state; never log another process's environment."""
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text()
        # comm may contain spaces/parentheses. Fields after its final ')' start at state (3).
        fields = raw.rsplit(")", 1)[1].split()
        if expected_start is not None and fields[19] != expected_start:
            return {"pid": int(pid), "identity_changed": True}
        return {"pid": int(pid), "state": fields[0], "ppid": int(fields[1]),
                "start_ticks": fields[19], "exit_code": int(fields[49])}
    except (OSError, IndexError, ValueError):
        return {"pid": int(pid), "unavailable": True}


def wrapper_events(directory, incarnation):
    """Read one exclusive metadata file, refusing links, shared modes and unbounded/raw records."""
    try:
        incarnation = str(uuid.UUID(incarnation)).upper()
        if not Path(directory).is_absolute():
            raise ValueError
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory_fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError
            descriptor = os.open(incarnation + ".log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory_fd)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise ValueError
                data = os.read(descriptor, 65537)
                if len(data) > 65536:
                    raise ValueError
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)
        events = []
        allowed = {"wrapper-start", "ssh-return", "osc-before", "osc-after",
                   "hold-before", "hold-return", "exit-intent"}
        for line in data.decode("ascii").splitlines():
            if not line.startswith("DIAG357 wrapper "):
                raise ValueError
            pairs = [word.split("=", 1) for word in line.split()[2:]]
            row = dict(pairs)
            if len(pairs) != 6 or set(row) != {"event", "incarnation", "pane", "nonceTag", "pid", "status"}:
                raise ValueError
            if (row["event"] not in allowed or row["incarnation"] != incarnation
                    or not re.fullmatch(r"[0-9a-f]{64}", row["nonceTag"])
                    or not re.fullmatch(r"[1-9][0-9]*", row["pid"])):
                raise ValueError
            row["pane"] = str(uuid.UUID(row["pane"])).upper()
            before_event = row["event"] in {"wrapper-start", "osc-before", "hold-before"}
            if (before_event and row["status"] != "na") or (
                    not before_event and not (row["status"].isdigit() and 0 <= int(row["status"]) <= 255)):
                raise ValueError
            events.append(row)
        return {"events": events}
    except (OSError, ValueError, UnicodeError, TypeError) as error:
        return {"events": [], "error": type(error).__name__}


def attachment_correlation(before, environment, panes, directory, proc_root="/proc"):
    """Only correlate a selected fixture SSH to the wrapper that recorded its matching opaque tags."""
    rejected = {"pid": before["pid"], "matched": False}
    try:
        names = (b"AGTERM_PANE_ID", b"AGTERM_DIAG357_INCARNATION", b"AGTERM_DIAG357_NONCE_TAG")
        tags = {name.decode(): value.decode("ascii") for item in environment
                for name, separator, value in [item.partition(b"=")] if separator and name in names}
        pane = str(uuid.UUID(tags["AGTERM_PANE_ID"])).upper()
        incarnation = str(uuid.UUID(tags["AGTERM_DIAG357_INCARNATION"])).upper()
        nonce = tags["AGTERM_DIAG357_NONCE_TAG"]
        if pane not in {str(uuid.UUID(value)).upper() for value in panes.values()} or before.get("ppid", 0) <= 1:
            return rejected
        records = wrapper_events(directory, incarnation)["events"]
        if not any(row["event"] == "wrapper-start" and int(row["pid"]) == before["ppid"]
                   and row["pane"] == pane and row["nonceTag"] == nonce for row in records):
            return rejected
        wrapper = process_status(before["ppid"], proc_root)
        current = process_status(before["pid"], proc_root, expected_start=before["start_ticks"])
        if "start_ticks" not in wrapper or current.get("ppid") != before["ppid"]:
            return rejected
        return {"pid": before["pid"], "matched": True, "pane": pane, "incarnation": incarnation,
                "nonceTag": nonce, "wrapper_before": wrapper}
    except (KeyError, ValueError, UnicodeError):
        return rejected


class ReconnectDiagnostics:
    """Retain transitions rather than identical polls, without changing the wait predicate."""

    def __init__(self):
        self.started = time.monotonic()
        self.snapshots = []
        self.signalled = []
        self.last = None
        self.directory = os.environ.get("AGTERM_DIAG357_DIR") if os.environ.get("AGTERM_DIAG357") == "1" else None
        self.correlations = []

    def correlate(self, before, environment, panes):
        if self.directory:
            self.correlations.append(attachment_correlation(before, environment, panes, self.directory))

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
        report = {"phase": phase, "elapsed": round(time.monotonic() - self.started, 3),
                  "ssh_down": os.path.exists(ssh_down), "snapshots": self.snapshots,
                  "signalled": self.signalled,
                  "processes_after": [(process_status(row["pid"], expected_start=row.get("start_ticks"))
                                       if self.directory else process_status(row["pid"])) for row in self.signalled],
                  "fixture_events": fixture_events}
        if self.correlations:
            correlated = []
            for row in self.correlations:
                result = dict(row)
                if row["matched"]:
                    wrapper = row["wrapper_before"]
                    result["wrapper_after"] = process_status(wrapper["pid"], expected_start=wrapper["start_ticks"])
                    result["wrapper_records"] = wrapper_events(self.directory, row["incarnation"])
                correlated.append(result)
            report["correlations"] = correlated
        return report


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
                diagnostics.correlate(before, environment, panes)
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
    if diagnostics.directory:
        print("RECONNECT_DIAGNOSTICS " + json.dumps(diagnostics.report("complete", ssh_down), sort_keys=True),
              flush=True)
    print("OK: SSH-255 pane loss published retry reason/count and reattached with stable pane identities")
