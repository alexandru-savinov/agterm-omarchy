#!/usr/bin/env python3
"""Drive the default-renderer install and the N-1 -> N upgrade inside the Hyprland session, one TSV row each.

`run` executes in the container as the session user, against the user's real default paths: prefix ~/.local,
the default state directory and socket. `verdict` runs on the host over the staged rows: every scenario row
must be present and PASS. Rows other than SCENARIO_ROWS (versions, digests, the GL renderer) are evidence only.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

FRESH_ROWS = ("fresh_install", "fresh_default_renderer", "fresh_window_mapped", "fresh_first_frame",
              "fresh_typed_command", "fresh_version", "fresh_quit", "fresh_uninstall")
UPGRADE_ROWS = ("upgrade_install_from", "upgrade_from_running", "upgrade_sessions", "upgrade_install_over_running",
                "upgrade_app_survived", "upgrade_new_ctl_drives_old_app", "upgrade_relaunch", "upgrade_version",
                "upgrade_sessions_restored", "upgrade_quit", "upgrade_uninstall")
SCENARIO_ROWS = FRESH_ROWS + UPGRADE_ROWS
# A red truecolour block painted through the PTY, then counted in the compositor's pixels.
RED_MIN_PIXELS = 4000
# A frame of one flat colour (or a handful) is not a rendered terminal.
MIN_DISTINCT_COLOURS = 16
SESSION_NAMES = ("upgrade-alpha", "upgrade-beta")


class Failed(Exception):
    pass


class Scenario:
    def __init__(self, args):
        self.args = args
        self.evidence = args.evidence
        self.tsv = self.evidence / "default-renderer.tsv"
        self.home = Path.home()
        self.prefix = self.home / ".local"
        self.app = None
        self.leg = ""
        self.marker_expected = "AGT43OK" if args.mutation == "wrong-marker" else "AGT42OK"
        self.min_colours = 10 ** 9 if args.mutation == "blank-threshold" else MIN_DISTINCT_COLOURS
        # The Omarchy session's toolkit environment (default/hypr/envs.lua), no AGTERM_* and no renderer override.
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("AGTERM_", "DR_", "GATE_")) and key != "LIBGL_ALWAYS_SOFTWARE"}
        self.env.update(GDK_BACKEND="wayland,x11,*", XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="Hyprland",
                        XDG_SESSION_DESKTOP="Hyprland")

    # Evidence

    def record(self, name, verdict, detail=""):
        line = f"{name}\t{verdict}{' ' + detail if detail else ''}".replace("\n", " ")
        with self.tsv.open("a") as out:
            out.write(line + "\n")
        print(line, flush=True)

    def step(self, name, action):
        try:
            detail = action() or ""
        except (Failed, AssertionError, OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
            self.record(name, "FAIL", f"{type(error).__name__}: {error}"[:600])
            raise Failed(name) from error
        self.record(name, "PASS", detail)

    def leg_dir(self):
        path = self.evidence / self.leg
        path.mkdir(exist_ok=True)
        return path

    # Processes and control

    def ctl(self, *args):
        completed = subprocess.run([str(self.prefix / "bin/agtermctl"), *args, "--json"], env=self.env, text=True,
                                   capture_output=True, timeout=20)
        with (self.leg_dir() / "control.jsonl").open("a") as out:
            out.write(json.dumps({"args": args, "exit": completed.returncode, "stdout": completed.stdout[-4000:],
                                  "stderr": completed.stderr[-2000:]}) + "\n")
        try:
            response = json.loads(completed.stdout)
        except ValueError as error:
            raise Failed(f"agtermctl {args[0]} printed no JSON (exit {completed.returncode}): "
                         f"{completed.stderr.strip()[-300:]}") from error
        if completed.returncode or not response.get("ok"):
            raise Failed(f"agtermctl {' '.join(args[:2])} failed: {response.get('error')}")
        return response.get("result") or {}

    def wait_for(self, test, reason, seconds=30):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            if self.app and self.app.poll() is not None:
                raise Failed(f"app exited with {self.app.returncode} while waiting for {reason}")
            try:
                value = test()
                if value:
                    return value
            except (Failed, OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
                last = error
            time.sleep(0.25)
        raise Failed(f"timed out waiting for {reason}; last error: {last}")

    def launch(self):
        log = (self.leg_dir() / "app.log").open("a")
        self.app = subprocess.Popen([str(self.prefix / "bin/agterm-linux")], env=self.env, stdout=log, stderr=log,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        self.wait_for(lambda: self.ctl("version"), "the default control socket to answer", 45)

    def client(self):
        clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"], text=True, timeout=15))
        return next((c for c in clients if c.get("pid") == self.app.pid and c.get("mapped")), None)

    def sessions(self):
        return [s for workspace in self.ctl("tree")["tree"]["workspaces"] for s in workspace["sessions"]]

    def session(self, session_id):
        return next((s for s in self.sessions() if s["id"] == session_id), {})

    def type_and_wait(self, session_id, text):
        """Type a command line followed by an execution marker; the marker proves the shell ran it."""
        done = self.home / f"executed-{uuid.uuid4().hex}"
        self.ctl("session", "type", f"{text}; printf done > {shlex.quote(str(done))}\n", "--target", session_id)
        self.wait_for(done.exists, "the shell to run the typed command", 20)

    def stop(self):
        """SIGTERM the app's own pid (the launcher execs the binary) and wait for it to exit."""
        pid = self.app.pid
        exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        os.kill(pid, signal.SIGTERM)
        try:
            code = self.app.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.app.kill()
            self.app.wait(timeout=10)
            raise Failed(f"pid {pid} ({exe}) ignored SIGTERM for 30 s") from None
        self.app = None
        return f"pid={pid} exe={exe} exit={code}"

    # Assertions

    def install(self, installer, stage, *flags):
        log = self.leg_dir() / f"install-{stage.parent.parent.name}.log"
        with log.open("w") as out:
            status = subprocess.run([installer, "--payload", str(stage), *flags], env=self.env, stdout=out,
                                    stderr=subprocess.STDOUT, timeout=300).returncode
        if status:
            raise Failed(f"install-linux.sh exited {status}: {log.read_text()[-400:]}")
        renderer = (self.prefix / "lib/agterm/share/agterm/RENDERER").read_text().strip()
        version = (self.prefix / "lib/agterm/share/agterm/VERSION").read_text().strip()
        if renderer != "default":
            raise Failed(f"installed RENDERER is {renderer!r}, not 'default'")
        return f"flags={' '.join(flags) or '(none)'} renderer={renderer} version={version}"

    def seed_settings(self):
        # A first launch opens an advisory welcome dialog over the window; it is not what this scenario tests.
        state = self.home / ".local/share/agterm"
        state.mkdir(parents=True, exist_ok=True)
        (state / "settings.json").write_text(json.dumps({"welcomeShown": True, "notificationsEnabled": False}))

    def default_renderer(self):
        environ = dict(item.split("=", 1) for item in
                       Path(f"/proc/{self.app.pid}/environ").read_bytes().decode(errors="replace").split("\0") if "=" in item)
        if "LIBGL_ALWAYS_SOFTWARE" in environ:
            raise Failed(f"the app runs with LIBGL_ALWAYS_SOFTWARE={environ['LIBGL_ALWAYS_SOFTWARE']}")
        maps = Path(f"/proc/{self.app.pid}/maps").read_text()
        mesa = sorted({os.path.basename(line.split()[-1]) for line in maps.splitlines()
                       if re.search(r"/(libgallium|libEGL_mesa|libGLX_mesa|dri/|libvulkan_)", line)})
        compat = "libagterm-egl-compat.so" in environ.get("LD_PRELOAD", "")
        return f"LIBGL_ALWAYS_SOFTWARE=unset egl_compat_preload={compat} mesa_mapped={','.join(mesa) or 'none'}"

    def window_mapped(self):
        window = self.wait_for(self.client, "the app window to map in Hyprland")
        if window.get("xwayland"):
            raise Failed("the window is XWayland, not native Wayland")
        return f"class={window.get('class')} at={window['at']} size={window['size']}"

    def capture(self, name):
        window = self.wait_for(self.client, "the app window to stay mapped")
        (x, y), (width, height) = window["at"], window["size"]
        path = self.leg_dir() / f"{name}.png"
        subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", str(path)], check=True, timeout=15)
        colours = int(subprocess.check_output(["magick", str(path), "-format", "%k", "info:"], text=True, timeout=30))
        data = subprocess.check_output(["magick", str(path), "-alpha", "off", "-depth", "8", "rgb:-"], timeout=30)
        red = sum(1 for offset in range(0, len(data), 3)
                  if data[offset] > 150 and data[offset + 1] < 80 and data[offset + 2] < 100)
        return colours, red

    def first_frame(self, session_id, name):
        self.wait_for(lambda: self.session(session_id).get("realized"), "the terminal GL surface to realize")
        blank = self.capture(f"{name}-before-paint")
        self.type_and_wait(session_id, "printf '\\033[2J\\033[H\\033[48;2;230;20;30m'; "
                                       "for i in {1..8}; do printf '%60s\\n' ' '; done; printf '\\033[0m'")
        result = {}

        def painted():
            result["colours"], result["red"] = self.capture(name)
            # Counted against the frame before the paint: Hyprland's own notices can carry red pixels too.
            return result["red"] - blank[1] >= RED_MIN_PIXELS and result["colours"] >= self.min_colours
        try:
            self.wait_for(painted, "a rendered, non-uniform frame with the painted block", 20)
        except Failed as error:
            raise Failed(f"{error}; colours={result.get('colours')} (min {self.min_colours}) "
                         f"red_px={result.get('red')} before={blank[1]} (min +{RED_MIN_PIXELS})") from None
        log = (self.leg_dir() / "app.log").read_text(errors="replace")
        for failure in ("GtkGLArea failed to create a GL context", "No EGL configuration available"):
            if failure in log:
                raise Failed(f"app.log reports {failure!r}")
        retry = "GTK EGL compatibility retry matched" in log
        return (f"colours={result['colours']} (min {self.min_colours}) red_px={result['red']} before={blank[1]} "
                f"(min +{RED_MIN_PIXELS}) before_paint_colours={blank[0]} egl_compat_retry_logged={retry}")

    def typed_command(self, session_id):
        # The typed text holds $((6*7)), not the expected marker, so only executed output can match.
        self.type_and_wait(session_id, "echo AGT$((6*7))OK")
        text = self.ctl("session", "text", "--target", session_id)["text"]
        (self.leg_dir() / f"session-text-{session_id[:8]}.txt").write_text(text)
        if self.marker_expected not in text:
            raise Failed(f"{self.marker_expected} is not in the session text")
        return f"typed 'echo AGT$((6*7))OK', read back {self.marker_expected}"

    def version(self, expected):
        result = self.ctl("version")
        running = result.get("app", {}).get("version")
        installed = (result.get("installed") or {}).get("version")
        if running != expected:
            raise Failed(f"agtermctl version reports {running!r}, expected {expected!r} (installed {installed!r})")
        return f"app={running} installed={installed}"

    def gl_renderer(self):
        completed = subprocess.run(["eglinfo", "-B", "-p", "wayland"], env=self.env, text=True, capture_output=True,
                                   timeout=30)
        (self.leg_dir() / "eglinfo-wayland.txt").write_text(completed.stdout + completed.stderr)
        renderers = sorted(set(re.findall(r"renderer: (.+)", completed.stdout)))
        self.record(f"{self.leg}_gl_renderer", "INFO",
                    f"{' | '.join(renderers) or 'none reported'} (Mesa on this display; vkms has no GPU)")

    def uninstall(self, installer):
        log = self.leg_dir() / "uninstall.log"
        with log.open("w") as out:
            status = subprocess.run([installer, "--prefix", str(self.prefix), "--uninstall", "--purge-state"],
                                    env=self.env, stdout=out, stderr=subprocess.STDOUT, timeout=120).returncode
        row = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("inspect-uninstall.py")),
                              "--prefix", str(self.prefix), "--user", os.environ["USER"], "--uninstall-status",
                              str(status), "--evidence", str(self.leg_dir())], text=True, capture_output=True)
        if not row.stdout.startswith("PASS"):
            left = (self.leg_dir() / "uninstall-left.txt").read_text() if (self.leg_dir() / "uninstall-left.txt").exists() else ""
            raise Failed(f"{row.stdout.strip()} {row.stderr.strip()} left: {' '.join(left.split()[:20])}")
        return row.stdout.strip()

    # Legs

    def prefix_absent(self):
        if self.prefix.exists() and any(self.prefix.iterdir()):
            raise Failed(f"{self.prefix} is not empty before install: {sorted(str(p) for p in self.prefix.rglob('*'))[:20]}")

    def fresh(self):
        self.leg = "fresh"
        a = self.args
        self.prefix_absent()
        self.step("fresh_install", lambda: self.install(a.to_installer, a.to_stage, "--default-rendering"))
        self.seed_settings()
        self.launch()
        self.step("fresh_default_renderer", self.default_renderer)
        self.step("fresh_window_mapped", self.window_mapped)
        initial = self.wait_for(self.sessions, "the initial session")[0]["id"]
        self.step("fresh_first_frame", lambda: self.first_frame(initial, "first-frame"))
        self.step("fresh_typed_command", lambda: self.typed_command(initial))
        self.gl_renderer()
        self.step("fresh_version", lambda: self.version(a.to_version))
        self.step("fresh_quit", self.stop)
        self.step("fresh_uninstall", lambda: self.uninstall(a.to_installer))

    def upgrade(self):
        self.leg = "upgrade"
        a = self.args
        self.prefix_absent()
        self.step("upgrade_install_from", lambda: self.install(a.from_installer, a.from_stage, "--default-rendering"))
        self.seed_settings()
        self.launch()
        initial = {}

        def from_running():
            self.window_mapped()
            initial["id"] = self.wait_for(self.sessions, "the initial session")[0]["id"]
            self.wait_for(lambda: self.session(initial["id"]).get("realized"), "the terminal GL surface to realize")
            return self.version(a.from_version)
        self.step("upgrade_from_running", from_running)
        beats = {name: self.home / f"heartbeat-{name}" for name in SESSION_NAMES}
        names = {}

        def open_sessions():
            for name in SESSION_NAMES:
                session_id = self.ctl("session", "new", "--name", name, "--cwd", str(self.home))["id"]
                self.wait_for(lambda: self.session(session_id).get("realized"), f"{name} to realize")
                self.ctl("session", "type", f"while :; do date +%s%N > {shlex.quote(str(beats[name]))}; sleep 0.5; done\n",
                         "--target", session_id)
            for name in SESSION_NAMES:
                self.wait_for(beats[name].exists, f"{name}'s running command")
            names["before"] = sorted(s["name"] for s in self.sessions())
            return f"sessions={names['before']} running=heartbeat loops in {', '.join(SESSION_NAMES)}"
        self.step("upgrade_sessions", open_sessions)
        pid = self.app.pid
        self.step("upgrade_install_over_running", lambda: self.install(a.to_installer, a.to_stage))

        def survived():
            if self.app.poll() is not None or not Path(f"/proc/{pid}").exists():
                raise Failed(f"pid {pid} exited during the install")
            self.wait_for(self.client, "the running window to stay mapped")
            before = {name: beats[name].read_text() for name in SESSION_NAMES}
            time.sleep(2)
            stale = [name for name in SESSION_NAMES if beats[name].read_text() == before[name]]
            if stale:
                raise Failed(f"running commands stopped in {stale}")
            exe = os.readlink(f"/proc/{pid}/exe")
            return f"pid={pid} still mapped; heartbeats advancing; exe={os.path.basename(exe)}"
        self.step("upgrade_app_survived", survived)

        def new_ctl():
            result = self.ctl("version")
            self.typed_command(initial["id"])
            return (f"installed agtermctl ({(self.prefix / 'lib/agterm/share/agterm/VERSION').read_text().strip()}) "
                    f"answered by running app={result.get('app', {}).get('version')} "
                    f"installed={(result.get('installed') or {}).get('version')}; "
                    f"typed command read back")
        self.step("upgrade_new_ctl_drives_old_app", new_ctl)
        stopped = self.stop_row("upgrade_relaunch")
        if stopped is None:
            return

        def relaunch():
            self.launch()
            self.window_mapped()
            # Only the selected session is on screen, so only its terminal surface realizes.
            shown = self.wait_for(lambda: next((s["id"] for s in self.sessions() if s.get("active")), None),
                                  "a selected restored session")
            detail = self.first_frame(shown, "relaunch-frame")
            self.typed_command(shown)
            return f"{stopped}; relaunched pid={self.app.pid}; {detail}"
        self.step("upgrade_relaunch", relaunch)
        self.step("upgrade_version", lambda: self.version(a.to_version))

        def restored():
            # The unnamed initial session's label is derived from its shell's title, which a restored session
            # not yet on screen has not sent; only explicit names are persisted state.
            after = sorted(s["name"] for s in self.sessions())
            missing = [name for name in SESSION_NAMES if name not in after]
            if missing or len(after) != len(names["before"]):
                raise Failed(f"sessions after relaunch {after}, before {names['before']}")
            return f"named={list(SESSION_NAMES)} present; count={len(after)}; before={names['before']} after={after}"
        self.step("upgrade_sessions_restored", restored)
        self.gl_renderer()
        self.step("upgrade_quit", self.stop)
        self.step("upgrade_uninstall", lambda: self.uninstall(a.to_installer))

    def stop_row(self, failing_row):
        try:
            return self.stop()
        except (Failed, OSError) as error:
            self.record(failing_row, "FAIL", f"SIGTERM of the upgraded-over app failed: {error}")
            return None

    def cleanup(self):
        if self.app and self.app.poll() is None:
            self.app.kill()
            self.app.wait(timeout=10)


def run(args):
    args.evidence.mkdir(parents=True, exist_ok=True)
    scenario = Scenario(args)
    scenario.record("mutation", "INFO", args.mutation)
    for leg in (scenario.fresh, scenario.upgrade):
        try:
            leg()
        except Failed as error:
            print(f"leg stopped at {error}", flush=True)
        finally:
            scenario.cleanup()
            # A leg that stopped early leaves its install behind; the next leg needs an empty prefix. What is
            # left after that is moved aside, not deleted, and its rows already say whether it counted.
            if (scenario.prefix / "lib/agterm").exists():
                subprocess.run([str(args.to_installer), "--prefix", str(scenario.prefix), "--uninstall", "--purge-state"],
                               env=scenario.env, capture_output=True, timeout=120)
            if scenario.prefix.exists() and any(scenario.prefix.iterdir()):
                aside = scenario.home / f"prefix-left-by-{scenario.leg or 'setup'}"
                scenario.prefix.rename(aside)
                scenario.record(f"{scenario.leg}_prefix_reset", "INFO",
                                f"moved {sorted(str(p.relative_to(aside)) for p in aside.rglob('*'))[:20]} aside")
    return verdict(scenario.tsv)


def verdict(tsv):
    rows = {}
    try:
        for line in tsv.read_text().splitlines():
            name, _, value = line.partition("\t")
            rows.setdefault(name, []).append(value)
    except OSError as error:
        print(f"no scenario rows: {error}")
        return 1
    # A repeated row passes only if every copy does.
    failed = [name for name in SCENARIO_ROWS if not rows.get(name) or not all(v.startswith("PASS") for v in rows[name])]
    for name in SCENARIO_ROWS:
        print(f"{name}\t{' / '.join(rows.get(name, ['MISSING']))}")
    print(f"default-renderer scenario: {'PASS' if not failed else 'FAIL ' + ','.join(failed)}")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--evidence", type=Path, required=True)
    for leg in ("from", "to"):
        run_parser.add_argument(f"--{leg}-stage", type=Path, required=True)
        run_parser.add_argument(f"--{leg}-installer", type=Path, required=True)
        run_parser.add_argument(f"--{leg}-version", required=True)
    run_parser.add_argument("--mutation", choices=("none", "wrong-marker", "blank-threshold"), default="none")
    verdict_parser = commands.add_parser("verdict")
    verdict_parser.add_argument("tsv", type=Path)
    args = parser.parse_args()
    sys.exit(run(args) if args.command == "run" else verdict(args.tsv))


if __name__ == "__main__":
    main()
