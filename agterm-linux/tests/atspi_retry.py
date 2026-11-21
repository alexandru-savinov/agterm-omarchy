"""The hosted GTK suite's narrow, fail-closed retry policy for issue #527."""

import os
from pathlib import Path
import platform
import subprocess


RETRYABLE = {
    ("aarch64", "leader-repeat"): "window switch reset the repeat prefix",
    ("aarch64", "v0430-gui"): "missing real GUI button: Move to…",
    ("x86_64", "remote-v0391"): "viewer did not reclaim the respawned pane",
}


def run_scenario(command, env):
    """Return (final status, extra attempts). Targeted runs never call this driver."""
    scenario = env["AGTERM_ATSPI_SCENARIO"]
    arch = platform.machine()
    signature = RETRYABLE.get((arch, scenario)) if env.get("GITHUB_ACTIONS") == "true" else None
    if signature is None:
        return subprocess.run(command, env=env).returncode, 0

    def attempt(number):
        attempt_env = env.copy()
        if artifact_root := env.get("AGTERM_UI_ARTIFACT_DIR"):
            directory = Path(artifact_root) / scenario / f"attempt-{number}"
            directory.mkdir(parents=True, exist_ok=True)
            attempt_env["AGTERM_UI_ARTIFACT_DIR"] = str(directory)
        # Keep AGTERM_UI_APP_STDERR shared with the shard so the CSS guard checks both attempts.
        result = subprocess.run(command, env=attempt_env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
        print(result.stdout, end="", flush=True)
        return result

    result = attempt(1)
    lines = result.stdout.splitlines()
    if (result.returncode != 1 or not lines or lines[-1] != f"FAIL: {signature}"
            or f"AssertionError: {signature}" not in lines):
        return result.returncode, 0

    print(f"AT-SPI retry: arch={arch} scenario={scenario} attempt=2/2 "
          f"first_status=1 assertion={signature!r}", flush=True)
    final = attempt(2)
    print(f"AT-SPI retry result: arch={arch} scenario={scenario} attempt=2/2 "
          f"status={final.returncode}", flush=True)
    return final.returncode, 1
