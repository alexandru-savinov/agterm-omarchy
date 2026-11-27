"""Exercise the installed Claude hooks as the package's unprivileged test user."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from contextlib import contextmanager


CTL = "/usr/bin/agtermctl"
WRAPPER = "/usr/bin/agterm-agent-status"
PAYLOADS = (
    ("PermissionRequest", "blocked", {"tool_name": "Bash", "tool_input": {"command": "printf fixture"}}),
    ("PostToolUse", "active", {"tool_name": "Bash", "tool_response": {"stdout": "fixture"}}),
    ("Stop", "completed", {"stop_hook_active": False, "last_assistant_message": "Done."}),
)
ORIGINAL = {
    "customSetting": {"enabled": True},
    "hooks": {
        "PermissionRequest": [{"hooks": [{"type": "command", "command": "herdr hook claude permission-request"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "fixture-audit", "timeout": 10}]}],
    },
}


def run(*args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, timeout=15, **kwargs)
    assert result.returncode == 0, (args, result.returncode, result.stdout, result.stderr)
    return result


def snapshot(home):
    return {path.relative_to(home): (path.read_bytes() if path.is_file() else None)
            for path in home.rglob("*")}


def configured_commands(settings):
    hooks = json.loads(settings.read_text())["hooks"]
    commands = {}
    for event, _, _ in PAYLOADS:
        handlers = [item["command"] for group in hooks[event] for item in group["hooks"]
                    if item.get("type") == "command" and item["command"].startswith(f"[ -x '{WRAPPER}' ]")]
        assert len(handlers) == 1, (event, handlers)
        commands[event] = handlers[0]
    return commands


def replay(command, event, fields, env):
    payload = {"session_id": "fixture-claude-conversation", "hook_event_name": event, "cwd": env["HOME"], **fields}
    result = run("/bin/sh", "-c", command, input=json.dumps(payload), env=env)
    assert (result.stdout, result.stderr) == ("", ""), (event, result)


def prepare(home, settings):
    for name in (".zshrc", ".bashrc", ".config/fish/config.fish", ".codex/config.toml",
                 ".pi/agent/settings.json", ".config/opencode/opencode.json"):
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture for {name}\n")
    install = (CTL, "integration", "install", "hooks", "--only", "claude")
    before_profile = snapshot(home)
    assert "Re-run after it exists" in run(*install).stdout
    assert snapshot(home) == before_profile, "activation created files before the Claude profile existed"

    settings.parent.mkdir()
    settings.write_text(json.dumps(ORIGINAL) + "\n")
    before = snapshot(home)
    run(*install)
    after = snapshot(home)
    path = settings.relative_to(home)
    backup = path.with_suffix(".json.bak")
    assert after[path] != before[path] and after[backup] == before[path]
    assert {k: v for k, v in after.items() if k not in (path, backup)} == {
        k: v for k, v in before.items() if k != path
    }
    configured = json.loads(after[path])
    for event, groups in ORIGINAL["hooks"].items():
        assert all(group in configured["hooks"][event] for group in groups), configured
    assert configured["customSetting"] == ORIGINAL["customSetting"], configured
    run(*install)
    assert snapshot(home) == after, "second activation changed HOME bytes"

    uninstall = (CTL, "integration", "uninstall", "hooks", "--only", "claude")
    run(*uninstall)
    disabled = snapshot(home)
    assert json.loads(disabled[path]) == ORIGINAL, "disable changed herdr, custom hooks, or settings"
    assert disabled[backup] == after[path]
    assert {k: v for k, v in disabled.items() if k not in (path, backup)} == {
        k: v for k, v in before.items() if k != path
    }
    run(*uninstall)
    assert snapshot(home) == disabled, "second disable changed HOME bytes"
    run(*install)
    print("OK: late profile, isolation, backups, idempotence and clean disable", flush=True)


@contextmanager
def background(args, env, log):
    with log.open("w") as output:
        process = subprocess.Popen(args, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            yield process
        except Exception:
            print(f"{log.name}:\n{log.read_text()}", flush=True)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def wait_for(predicate, failure, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError(failure)


def event_cursor(path):
    # Start the CLI stream from a cursor taken before replay, even if the child hasn't polled yet.
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(path)
        client.sendall(b'{"cmd":"events.read"}\n')
        with client.makefile() as stream:
            response = json.loads(stream.readline())
    assert response["ok"], response
    return response["result"]["events"]


def statuses(log, session):
    return [record["payload"]["status"] for record in map(json.loads, log.read_text().splitlines())
            if record["kind"] == "status" and record.get("session", "").lower() == session.lower()]


def verify_events(work, home, commands, bridge, env):
    address = env["AGTERM_SOCKET"]
    with background(["/usr/bin/agterm-linux"], env, work / "agterm.log") as app:
        def ready():
            assert app.poll() is None, "desktop process exited"
            if not Path(address).is_socket():
                return False
            probe = subprocess.run([CTL, "tree", "--json", "--socket", address],
                                   env=env, capture_output=True, timeout=5)
            return probe.returncode == 0 and json.loads(probe.stdout)["ok"]

        wait_for(ready, "isolated desktop control socket did not answer", seconds=30)
        created = run(CTL, "session", "new", "--cwd", str(home), "--name", "Claude hook replay",
                      "--no-select", "--json", "--socket", address, env=env)
        session = json.loads(created.stdout)["result"]["id"]
        cursor = event_cursor(address)
        args = [CTL, "events", "--json", "--kind", "status", "--socket", address,
                "--run", cursor["run"], "--after", str(cursor["next"])]
        log = work / "events.jsonl"
        hook_env = dict(env, AGTERM_SESSION_ID=session)
        expected = []
        with background(args, env, log) as stream:
            def observed():
                assert stream.poll() is None, "agtermctl events exited"
                return statuses(log, session) == expected

            event, status, fields = PAYLOADS[0]
            expected.append(status)
            replay("exit 0", event, fields, hook_env)
            try:
                wait_for(observed, "missing blocked event", seconds=1)
            except TimeoutError:
                print("OK: the event assertion rejected a hook that never called the wrapper", flush=True)
            else:
                raise AssertionError("disabled PermissionRequest unexpectedly posted blocked")
            expected.clear()

            for event, status, fields in PAYLOADS:
                expected.append(status)
                replay(commands[event], event, fields, hook_env)
                wait_for(observed, f"expected {expected} for {session}")
                result = run("/usr/bin/python3", "-I", str(bridge), "snapshot", "--cli", CTL, env=env)
                data = json.loads(result.stdout)
                assert data["online"] and not data["warning"], data
                rows = [row for row in data["sessions"] if row["id"].lower() == session.lower()]
                assert len(rows) == 1 and rows[0]["status"] == status, data
                print(f"OK: {event} -> {status} in events and pinned bridge JSON", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("installed", "removed"))
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    assert os.geteuid() != 0, "run hook replay as the unprivileged fixture user"
    home = Path(os.environ["HOME"])
    settings = home / ".claude/settings.json"
    if args.phase == "installed":
        assert Path(WRAPPER).is_file()
        prepare(home, settings)
        env = dict(os.environ, GDK_BACKEND="x11", LIBGL_ALWAYS_SOFTWARE="1", GTK_A11Y="none")
        for name in ("config", "data", "state", "cache", "runtime"):
            path = args.work / name
            path.mkdir(mode=0o700)
            env[f"XDG_{name.upper()}_DIR" if name == "runtime" else f"XDG_{name.upper()}_HOME"] = str(path)
        address = str(args.work / "agterm.sock")
        env.update(AGTERM_SOCKET=address, AGTERM_CONTROL_SOCKET=address, AGTERM_STATE_DIR=str(args.work / "state"))
        verify_events(args.work, home, configured_commands(settings), args.work / "agterm_bridge.py", env)
    else:
        assert not Path(WRAPPER).exists() and not Path(WRAPPER).is_symlink(), WRAPPER
        before = snapshot(home)
        for event, _, fields in PAYLOADS:
            replay(configured_commands(settings)[event], event, fields,
                   dict(os.environ, AGTERM_SESSION_ID="fixture", AGTERM_SOCKET=str(args.work / "missing.sock")))
        assert snapshot(home) == before
        print("OK: saved hook commands are silent and exit 0 after pacman -R", flush=True)


if __name__ == "__main__":
    main()
