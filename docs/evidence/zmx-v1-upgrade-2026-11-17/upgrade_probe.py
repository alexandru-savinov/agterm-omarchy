#!/usr/bin/env python3
"""Upgrade probe: a Live session made by the old payload, then the new payload on the same private state.

usage: upgrade_probe.py RUN_DIR OLD_TARBALL NEW_TARBALL
Everything lives under RUN_DIR (private prefix, HOME, XDG dirs, agterm state); the desktop's own agterm
install, state and daemons are never touched. Prints a JSON report and leaves artifacts in RUN_DIR/out.
"""
import json, os, re, shlex, signal, subprocess, sys, tarfile, time, uuid
from pathlib import Path

run = Path(sys.argv[1]).resolve()
assert re.fullmatch(r"/(var/)?tmp/agt-upg-[a-z0-9]+", str(run)), run
old_tar, new_tar = sys.argv[2], sys.argv[3]
out = run / "out"; out.mkdir()
prefix = run / "prefix"; prefix.mkdir()
state = run / "state"; state.mkdir()
home = run / "home"; home.mkdir()
(state / "windows").mkdir()
# Live mode needs zsh or fish; this user's login shell is bash, so name zsh through agterm's ghostty config.
(state / "config").mkdir()
(state / "config" / "ghostty.conf").write_text("command = /usr/bin/zsh\n")
(home / ".zshrc").write_text("PS1='upg%# '\n")
(state / "settings.json").write_text(json.dumps({
    "welcomeShown": True, "restoreMode": "live", "closeGraceUndoEnabled": False, "notificationsEnabled": False}))

hypr = subprocess.check_output(["pgrep", "-x", "Hyprland"], text=True).split()[0]
desktop = dict(item.split("=", 1) for item in Path(f"/proc/{hypr}/environ").read_bytes().decode().split("\0") if "=" in item)
env = {k: desktop[k] for k in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR",
                               "DBUS_SESSION_BUS_ADDRESS") if k in desktop}
env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
runtime = Path(env["XDG_RUNTIME_DIR"])
env.setdefault("WAYLAND_DISPLAY", sorted(p.name for p in runtime.glob("wayland-*") if not p.name.endswith(".lock"))[0])
env.setdefault("HYPRLAND_INSTANCE_SIGNATURE", sorted(p.name for p in (runtime / "hypr").iterdir())[0])
env.update(PATH="/usr/local/bin:/usr/bin:/bin", LANG="C.UTF-8", SHELL="/bin/bash", USER=os.environ["USER"],
           HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
           XDG_CACHE_HOME=str(home / ".cache"), XDG_STATE_HOME=str(home / ".local/state"),
           AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / "agterm.sock"),
           AGTERM_APP_ID="io.github.melonamin.agterm.upgrade.r" + uuid.uuid4().hex,
           GDK_BACKEND="wayland", XDG_SESSION_TYPE="wayland",
           LIBGL_ALWAYS_SOFTWARE="1")  # the VM has no GPU; its own agterm runs the same way
report = {"run": str(run), "steps": []}
app = None


def note(step, **data):
    data["step"] = step; data["t"] = round(time.monotonic() - T0, 2)
    report["steps"].append(data); print(json.dumps(data), flush=True)


def stage(tarball):
    current = prefix / "agterm-stage"
    if current.exists():
        current.rename(prefix / f"replaced-{uuid.uuid4().hex[:6]}")
    with tarfile.open(tarball) as archive:
        archive.extractall(prefix, filter="tar")
    return current / "bin" / "agterm-linux", current / "bin" / "agtermctl"


def ctl(*args, timeout=10):
    started = time.monotonic()
    done = subprocess.run([str(CTL), *args, "--socket", env["AGTERM_CONTROL_SOCKET"], "--json"], env=env,
                          text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    result = json.loads(done.stdout) if done.stdout.strip() else {"ok": False, "stderr": done.stderr}
    result["_seconds"] = round(time.monotonic() - started, 2)
    return result


def wait_for(test, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if app.poll() is not None:
            return None
        try:
            value = test()
            if value:
                return value
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError):
            pass
        time.sleep(0.3)
    return None


def sessions():
    tree = ctl("tree")["result"]["tree"]
    return [s for w in tree["workspaces"] for s in w["sessions"]]


def launch(name):
    global app
    log = (out / f"{name}.log").open("w")
    app = subprocess.Popen([str(BIN)], env=env, stdout=log, stderr=log, start_new_session=True)
    up = wait_for(lambda: Path(env["AGTERM_CONTROL_SOCKET"]).is_socket() and ctl("tree").get("ok"), 30)
    note(f"{name}: launched", pid=app.pid, control_socket=bool(up))
    return bool(up)


def window():
    clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"], env={**os.environ, **env}, text=True))
    return next((c for c in clients if c.get("pid") == app.pid and c.get("mapped")), None)


def capture(name):
    win = wait_for(window, 15)
    if not win:
        note(f"capture {name}", error="no mapped window"); return
    x, y = win["at"]; w, h = win["size"]
    path = out / f"{name}.png"
    subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", str(path)], env={**os.environ, **env}, timeout=15)
    note(f"capture {name}", path=str(path), window=win.get("address"))


def zmx(exe, *args):
    done = subprocess.run([exe, *args], env={**env, "ZMX_DIR": SOCKDIR}, text=True, capture_output=True,
                          timeout=10, stdin=subprocess.DEVNULL)
    return {"rc": done.returncode, "stdout": done.stdout[-2000:], "stderr": done.stderr[-600:]}


def daemon_pids():
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            environ = (entry / "environ").read_bytes().split(b"\0")
            cmdline = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if f"ZMX_DIR={SOCKDIR}".encode() in environ and cmdline and cmdline[0].endswith(b"zmx"):
            stat = (entry / "stat").read_text()
            tty = stat[stat.rindex(")") + 2:].split()[4]
            pids.append((int(entry.name), b" ".join(cmdline).decode(errors="replace")[:160], "tty_nr=" + tty))
    return pids


T0 = time.monotonic()
try:
    BIN, CTL = stage(old_tar)
    launch("old")
    first = wait_for(lambda: [s for s in sessions() if s.get("realized")], 30)
    sid = first[0]["id"]
    inventory = ctl("zmx", "list")["result"]["zmx"]
    SOCKDIR = inventory["endpoint"]["socketDirectory"]
    OLD_ZMX = inventory["endpoint"]["executable"]
    pid_file = run / "shell-pid-old"
    ctl("session", "type", "printf 'BEFORE-UPGRADE-MARK\\n'; printf '%s' \"$$\" > " + shlex.quote(str(pid_file)) + "\n",
        "--target", sid)
    wait_for(pid_file.exists, 10)
    note("old: session", session=sid, backedByZmx=first[0].get("backedByZmx"), shell_pid=pid_file.read_text(),
         zmx_endpoint=inventory["endpoint"], entries=inventory["entries"],
         text_has_mark="BEFORE-UPGRADE-MARK" in ctl("session", "text", "--target", sid)["result"].get("text", ""))
    capture("1-old-before-upgrade")
    app.terminate(); app.wait(timeout=20)
    note("old: SIGTERM quit", returncode=app.returncode, daemons=daemon_pids(),
         old_zmx_list=zmx(str(OLD_ZMX), "list"))

    BIN, CTL = stage(new_tar)
    log = (out / "new.log").open("w")
    t_spawn = time.monotonic()
    app = subprocess.Popen([str(BIN)], env=env, stdout=log, stderr=log, start_new_session=True)
    mapped = wait_for(window, 20)
    note("new: window mapped", seconds_after_spawn=round(time.monotonic() - t_spawn, 2), mapped=bool(mapped))
    capture("2a-new-at-map")
    time.sleep(1.5)
    capture("2b-new-map-plus-1.5s")
    started = wait_for(lambda: Path(env["AGTERM_CONTROL_SOCKET"]).is_socket() and ctl("tree").get("ok"), 30)
    note("new: launched", pid=app.pid, control_socket=bool(started),
         seconds_after_spawn=round(time.monotonic() - t_spawn, 2))
    note("new: zmx", version=zmx(str(prefix / "agterm-stage/bin/zmx"), "version"),
         same_path_as_old=str(prefix / "agterm-stage/bin/zmx") == str(OLD_ZMX))
    if started:
        realized = wait_for(lambda: [s for s in sessions() if s["id"] == sid and s.get("realized")], 30)
        tree = ctl("tree")
        inv = ctl("zmx", "list")
        note("new: liveReset readback", tree_liveReset=tree.get("result", {}).get("tree", {}).get("liveReset"),
             zmx_liveReset=inv.get("result", {}).get("zmx", {}).get("liveReset"))
        note("new: restored", realized=bool(realized), tree_seconds=tree["_seconds"],
             sessions=[{k: s.get(k) for k in ("id", "name", "realized", "backedByZmx")} for s in sessions()],
             zmx_list=inv.get("result", inv))
        time.sleep(3)
        capture("2-new-after-relaunch")
        text = ctl("session", "text", "--target", sid)
        note("new: screen read-back", ok=text.get("ok"), text=text.get("result", {}).get("text", "")[-400:],
             error=text.get("error"))
        after = run / "shell-pid-new"
        typed = ctl("session", "type", "printf 'AFTER-UPGRADE-MARK\\n'; printf '%s' \"$$\" > " +
                    shlex.quote(str(after)) + "\n", "--target", sid)
        executed = wait_for(after.exists, 8)
        time.sleep(1)
        text2 = ctl("session", "text", "--target", sid)
        note("new: typed into restored pane", type_ok=typed.get("ok"), executed=bool(executed),
             shell_pid=after.read_text() if executed else None,
             text_has_after_mark="AFTER-UPGRADE-MARK" in text2.get("result", {}).get("text", ""),
             text=text2.get("result", {}).get("text", "")[-400:])
        capture("3-new-after-typing")
        note("new: daemons", daemons=daemon_pids(), new_list=zmx(str(inv["result"]["zmx"]["endpoint"]["executable"])
                                                                  if inv.get("ok") else OLD_ZMX, "list"))
finally:
    if app and app.poll() is None:
        app.terminate()
        try:
            app.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app.kill(); app.wait()
    left = daemon_pids() if "SOCKDIR" in globals() else []
    for pid, *_ in left:
        try:
            os.killpg(os.getpgid(pid), signal.SIGHUP)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(1)
    note("cleanup", killed=left, remaining=daemon_pids() if "SOCKDIR" in globals() else [])
    (out / "report.json").write_text(json.dumps(report, indent=2))
