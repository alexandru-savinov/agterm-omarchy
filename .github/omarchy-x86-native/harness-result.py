#!/usr/bin/env python3
"""Record the ordinary and Live harness results with the required EGL retry evidence."""

import json
from pathlib import Path
import re
import sys


def harness_result(root, expected, status, name, gtk_configs):
    if not re.fullmatch(r"[0-9]+", gtk_configs):
        return f"{name}\tFAIL exit={status} egl_retry=unknown_gtk_configs"

    try:
        result = json.loads((root / "result.json").read_text())
        cleanup = json.loads((root / "cleanup.json").read_text())
    except (OSError, ValueError) as error:
        return f"{name}\tFAIL exit={status} no result ({type(error).__name__})"

    checks = result.get("checks") or result.get("completed_checks") or []
    ok = status == 0 and result.get("passed") is True and len(checks) == expected and cleanup.get("passed") is True
    egl = ""
    if not gtk_configs.strip("0"):
        # The harness combines the application's stdout and stderr in app.log.
        try:
            with (root / "app.log").open("rb") as log:
                retry = any(b"GTK EGL compatibility retry matched" in line for line in log)
        except OSError:
            retry = False
        ok = ok and retry
        egl = " egl_retry=logged" if retry else " egl_retry=missing_from_app.log"

    return (f"{name}\t{'PASS' if ok else 'FAIL'} {len(checks)}/{expected} exit={status} "
            f"cleanup={cleanup.get('passed')} daemons={cleanup.get('tracked_daemon_count')} "
            f"harness_matches_commit={result['identity']['harness'].get('matches_commit')}{egl}")


if __name__ == "__main__":
    root, expected, status, name, gtk_configs = sys.argv[1:]
    print(harness_result(Path(root), int(expected), int(status), name, gtk_configs))
