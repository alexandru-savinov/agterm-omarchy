"""The hosted GTK suite's narrow, fail-closed retry policy for issue #527."""

from pathlib import Path
import platform
import re
import subprocess


RETRYABLE = {
    ("aarch64", "leader-repeat"): "window switch reset the repeat prefix",
    ("aarch64", "v0430-gui"): "missing real GUI button: Move to…",
    ("aarch64", "window-close-cancel"): "session {session_id} never answered before",
    ("x86_64", "remote-v0391"): "viewer did not reclaim the respawned pane",
}
SESSION_ID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def report_retry(message, env):
    print(message, flush=True)
    if summary := env.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as file:
            file.write(f"- {message}\n")


def run_scenario(command, env):
    """Return (final status, extra attempts). Targeted runs never call this driver."""
    scenario = env["AGTERM_ATSPI_SCENARIO"]
    arch = platform.machine()
    signature = RETRYABLE.get((arch, scenario)) if env.get("GITHUB_ACTIONS") == "true" else None
    if signature is None:
        return subprocess.run(command, env=env).returncode, 0

    pattern = re.escape(signature).replace(re.escape("{session_id}"), SESSION_ID)

    def attempt(number):
        attempt_env = dict(env, PYTHONUNBUFFERED="1")
        last_line = assertion = None
        if artifact_root := env.get("AGTERM_UI_ARTIFACT_DIR"):
            directory = Path(artifact_root) / scenario / f"attempt-{number}"
            directory.mkdir(parents=True, exist_ok=True)
            attempt_env["AGTERM_UI_ARTIFACT_DIR"] = str(directory)
        # The shard retains the streamed trace and shared app stderr, including the CSS guard input.
        with subprocess.Popen(command, env=attempt_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace") as child:
            for line in child.stdout:
                print(line, end="", flush=True)
                last_line = line.rstrip("\r\n")
                if last_line.startswith("AssertionError: "):
                    assertion = last_line.removeprefix("AssertionError: ")
            status = child.wait()
        match = (status == 1 and assertion is not None and last_line == f"FAIL: {assertion}"
                 and re.fullmatch(pattern, assertion))
        return status, assertion if match else None

    status, assertion = attempt(1)
    if assertion is None:
        return status, 0

    report_retry(f"AT-SPI retry: arch={arch} scenario={scenario} attempt=2/2 "
                 f"first_status=1 assertion={assertion!r}", env)
    if output := env.get("GITHUB_OUTPUT"):
        # Both shards can append the same value. Emit before attempt 2 so a hang retains diagnostics.
        with Path(output).open("a", encoding="utf-8") as file:
            file.write("retried=true\n")
    final, _ = attempt(2)
    report_retry(f"AT-SPI retry result: arch={arch} scenario={scenario} attempt=2/2 status={final}", env)
    return final, 1
