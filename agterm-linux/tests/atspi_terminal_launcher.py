"""Exercise the launcher against an isolated GTK app, and check the real class under Hyprland."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from atspi_desktop_actions import active_session_cwd, invoke_arguments, session_ids
from atspi_smoke import BIN, app_stderr_sink, launch, named, stop, wait_for, window_list


def open_ids(env):
    return {row["id"] for row in window_list(env) if row["open"]}


def wait_for_new_window(env, previous, reason):
    return wait_for(lambda: next(iter(open_ids(env) - previous), None), reason)


def ephemeral_process(env, directory, program, args, title, app_id="org.omarchy.7z"):
    sink = app_stderr_sink()
    try:
        return subprocess.Popen(
            [BIN, f"--app-id={app_id}", f"--title={title}", "--working-directory=relative", "-e", program, *args],
            cwd=directory, env=env, stdout=subprocess.DEVNULL, stderr=sink,
        )
    finally:
        if sink is not subprocess.DEVNULL:
            sink.close()


def wayland_window(pid, title, app_id=None):
    def matched():
        clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"], text=True, timeout=5))
        return any(client.get("pid") == pid and client.get("title") == title
                   and (app_id is None or (client.get("class") == app_id and client.get("initialClass") == app_id))
                   for client in clients)

    wait_for(matched, "Hyprland did not receive the requested initial class and title")


def rss_kib(pid):
    fields = Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines()
    return next(int(field.split()[1]) for field in fields if field.startswith("VmRSS:"))


def verify_launcher_review(env):
    root = Path(env["AGTERM_STATE_DIR"] + "-launcher-review")
    relative = root / "relative"
    relative.mkdir(parents=True)
    failures = []
    for name, check in (
        ("notification settings", verify_notification_settings),
        ("inherited pane environment", verify_inherited_pane_environment),
        ("window close", lambda local, fixture: verify_ephemeral_cleanup(local, fixture, close=True)),
        ("SIGTERM", lambda local, fixture: verify_ephemeral_cleanup(local, fixture, close=False)),
    ):
        fixture = root / name.replace(" ", "-")
        fixture.mkdir()
        state = fixture / "state"
        local = dict(env, AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"))
        try:
            check(local, fixture)
            print(f"OK: ephemeral {name}", flush=True)
        except (AssertionError, subprocess.SubprocessError) as error:
            failure = f"ephemeral {name}: {error}"
            print(f"FAIL: {failure}", flush=True)
            failures.append(failure)
    assert not failures, "\n".join(failures)


def run_ephemeral_probe(env, fixture, code, *args):
    process = ephemeral_process(env, fixture.parent, sys.executable, ["-c", code, *args], "Launcher review")
    try:
        assert process.wait(timeout=12) == 0, "probe process did not exit successfully"
    finally:
        if process.poll() is None:
            stop(process)


def verify_notification_settings(env, fixture):
    state = Path(env["AGTERM_STATE_DIR"])
    config = state / "config"
    config.mkdir(parents=True)
    (config / "ghostty.conf").write_text("desktop-notifications = true\n", encoding="utf-8")
    settings = state / "settings.json"
    invalid_settings = b"{invalid"
    settings.write_bytes(invalid_settings)
    probe = "import os, time; os.write(1, b'\\x1b]9;Launcher test notification\\x07'); time.sleep(1)"
    run_ephemeral_probe(env, fixture, probe)
    assert sorted(item.name for item in state.iterdir()) == ["config", "settings.json"], "OSC 9 changed the settings folder"
    assert settings.read_bytes() == invalid_settings, "OSC 9 changed the settings file"


def verify_inherited_pane_environment(env, fixture):
    names = ("AGTERM_SESSION_ID", "AGTERM_PANE_ID", "AGTERM_WINDOW_ID", "AGTERM_SOCKET")
    local = dict(env, **{name: "fixture-value" for name in names})
    local["AGTERM_SOCKET"] = str(fixture / "unused.sock")
    marker = fixture / "env.json"
    probe = "import json, os, sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps(sorted(os.environ)))"
    run_ephemeral_probe(local, fixture, probe, marker)
    leaked = set(names) & set(json.loads(marker.read_text(encoding="utf-8")))
    assert not leaked, f"child inherited {', '.join(sorted(leaked))}"


def verify_ephemeral_cleanup(env, fixture, *, close):
    runtime = fixture / "runtime"
    runtime.mkdir(mode=0o700)
    local = dict(env, XDG_RUNTIME_DIR=str(runtime))
    display = env.get("WAYLAND_DISPLAY")
    native = bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"))
    if native and display and not display.startswith("/"):
        local["WAYLAND_DISPLAY"] = str(Path(env["XDG_RUNTIME_DIR"]) / display)
    title = "Close ephemeral launcher" if close else "Terminate ephemeral launcher"
    marker = fixture / "pid"
    probe = "import os, sys, time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    process = ephemeral_process(local, fixture.parent, sys.executable, ["-c", probe, marker], title)
    try:
        wait_for(lambda: marker.exists() and marker.stat().st_size, "command did not start")
        child = Path("/proc") / marker.read_text(encoding="utf-8")
        assert child.exists(), "command already exited before cleanup"
        assert list(runtime.glob("agterm-ghostty-*")), "fixture did not initialize Ghostty runtime storage"
        if close and native:
            wayland_window(process.pid, title)
            subprocess.run(["hyprctl", "dispatch", "closewindow", f"pid:{process.pid}"], check=True, timeout=5)
        elif close:
            windows = subprocess.check_output(
                ["xdotool", "search", "--sync", "--onlyvisible", "--pid", str(process.pid)], text=True, timeout=8,
            ).splitlines()
            subprocess.run(["xdotool", "windowactivate", "--sync", windows[-1], "key", "--clearmodifiers", "alt+F4"],
                           check=True, timeout=5)
        else:
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=12) == 0, "ephemeral launcher exit status was nonzero"
        wait_for(lambda: not child.exists(), "ephemeral child survived window teardown")
        assert not list(runtime.glob("agterm-ghostty-*")), "ephemeral launcher left its Ghostty runtime directory"
        assert not Path(env["AGTERM_STATE_DIR"]).exists(), "ephemeral launcher created persistent state"
    finally:
        if process.poll() is None:
            stop(process)


def verify_terminal_launcher(env):
    root = Path(env["AGTERM_STATE_DIR"] + "-terminal-launcher")
    state, caller = root / "state", root / "caller"
    relative = caller / "relative"
    relative.mkdir(parents=True)
    launch_env = dict(env, AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"),
                      AGTERM_APP_ID=f'{env["AGTERM_APP_ID"]}.terminalLaunch')
    main, app = launch(launch_env, [f"--working-directory={caller}", "--title=Cold launcher"])
    hyprland = bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"))
    children = []
    try:
        cold = wait_for_new_window(launch_env, set(), "cold launcher did not open a window")
        wait_for(lambda: active_session_cwd(launch_env, cold) == str(caller), "cold launcher lost its cwd")
        wait_for(lambda: named(app, "Cold launcher", role="frame"), "cold launcher lost its title")
        assert open_ids(launch_env) == {cold}

        warm_base_rss = rss_kib(main.pid) if hyprland else None
        warm_start = time.monotonic()
        invoke_arguments(launch_env, "--working-directory=relative", "--title=Warm launcher", cwd=caller)
        if hyprland:
            wayland_window(main.pid, "Warm launcher")
            warm_metrics = (time.monotonic() - warm_start, rss_kib(main.pid) - warm_base_rss)
        warm = wait_for_new_window(launch_env, {cold}, "warm launcher added a session instead of a window")
        wait_for(lambda: active_session_cwd(launch_env, warm) == str(relative), "warm launch lost the forwarded cwd")
        wait_for(lambda: named(app, "Warm launcher", role="frame"), "warm launcher lost its title")

        invoke_arguments(launch_env, "--working-directory=missing", cwd=caller)
        fallback = wait_for_new_window(launch_env, {cold, warm}, "missing-directory launch did not open a window")
        wait_for(lambda: active_session_cwd(launch_env, fallback) == str(caller), "missing directory did not fall back")
        baseline = {window: session_ids(launch_env, window) for window in open_ids(launch_env)}
        invoke_arguments(launch_env)
        assert {window: session_ids(launch_env, window) for window in open_ids(launch_env)} == baseline

        # Literal shell metacharacters, empty arguments, and option-like words must reach the child unchanged.
        payload = ["--title=literal", "", "two words", "a'b", '\\slash', '$(false)', '$HOME', '*']
        probe = root / "probe.py"
        probe.write_text(
            "import json, os, time, sys\nfrom pathlib import Path\n"
            "Path(sys.argv[1]).write_text(json.dumps([os.getcwd(), sys.argv[3:]]))\n"
            "for _ in range(600):\n"
            "    if Path(sys.argv[2]).exists(): break\n"
            "    time.sleep(0.05)\n", encoding="utf-8",
        )
        cold_ephemeral_state = root / "unwritten"
        cold_ephemeral_env = dict(launch_env, AGTERM_STATE_DIR=str(cold_ephemeral_state),
                                 AGTERM_CONTROL_SOCKET=str(cold_ephemeral_state / "agterm.sock"))
        for index, child_env in enumerate((launch_env, cold_ephemeral_env)):
            marker, release = root / f"result-{index}.json", root / f"release-{index}"
            title = f"Ephemeral launcher {index}"
            child = ephemeral_process(child_env, caller, sys.executable, [probe, marker, release, *payload], title)
            children.append((child, release))
            wait_for(lambda: marker.exists() and marker.stat().st_size, "ephemeral -e command never ran")
            assert json.loads(marker.read_text(encoding="utf-8")) == [str(relative), payload]
            assert child.poll() is None, "ephemeral forwarding process exited instead of owning a window"
            if hyprland:
                wayland_window(child.pid, title, "org.omarchy.7z")

        assert all(child.poll() is None for child, _ in children), "custom class launches were coalesced"
        for child, release in children:
            release.touch()
            assert child.wait(timeout=12) == 0, "ephemeral process did not quit with its command"
        assert not cold_ephemeral_state.exists(), "ephemeral launcher wrote persistent app state"
        assert {window: session_ids(launch_env, window) for window in open_ids(launch_env)} == baseline

        cold_ephemeral_state.mkdir()
        settings = cold_ephemeral_state / "settings.json"
        settings.write_bytes(b"{invalid")
        marker, release = root / "corrupt-settings-result.json", root / "corrupt-settings-release"
        release.touch()
        child = ephemeral_process(cold_ephemeral_env, caller, sys.executable, [probe, marker, release], "Read-only settings")
        children.append((child, release))
        assert child.wait(timeout=12) == 0 and marker.exists(), "ephemeral launch failed with malformed settings"
        assert list(cold_ephemeral_state.iterdir()) == [settings] and settings.read_bytes() == b"{invalid"

        btop = shutil.which("btop") if hyprland else None
        if btop:
            started = time.monotonic()
            process = ephemeral_process(cold_ephemeral_env, caller, btop, [], "Launcher cost probe", "org.omarchy.btop")
            try:
                wayland_window(process.pid, "Launcher cost probe", "org.omarchy.btop")
                print(f"Native launcher cost: warm {warm_metrics[0]:.3f}s, RSS delta {warm_metrics[1]} KiB; "
                      f"ephemeral btop {time.monotonic() - started:.3f}s, RSS {rss_kib(process.pid)} KiB")
            finally:
                if process.poll() is None:
                    stop(process)
        print("OK: cold/warm launcher windows, literal argv, independent ephemeral processes, and unchanged main sessions")
    finally:
        for child, _ in children:
            if child.poll() is None:
                stop(child)
        stop(main)
