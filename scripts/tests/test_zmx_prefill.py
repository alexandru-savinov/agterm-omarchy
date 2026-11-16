#!/usr/bin/env python3
"""Real zmx: a line typed into a newly created daemon's shell without a line terminator waits for Enter.

This mirrors the Linux app after a Live daemon was lost: a plain `zmx attach <name>` creates the daemon
with the login shell, and the captured command arrives as initial input with no newline. Set
AGTERM_IDENTITY_TEST_ZMX to a real zmx executable (and have zsh on PATH); the cases are skipped otherwise.
"""
import os
import json
import sys
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import uuid


ZMX = os.environ.get("AGTERM_IDENTITY_TEST_ZMX", "")
ZSH = shutil.which("zsh")
# Typed text shows the expression; only execution prints the product.
LINE = b"print -r -- $(( 6 * 7 ))ZQ"
RAN = b"42ZQ"
REAP_SECONDS = 5


def reap(pid, seconds):
    """Wait for an unreaped child of this process; False if it is still running at the deadline."""
    deadline = time.monotonic() + seconds
    while os.waitpid(pid, os.WNOHANG)[0] != pid:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def close_client(pid, fd, seconds=REAP_SECONDS):
    """Close an attach client's pty master, then SIGKILL and reap that child within a deadline."""
    # Darwin keeps a killed zmx client exiting while output it wrote is unread on the master (#422).
    try:
        os.close(fd)
    finally:
        # waitpid raises for a pid that is not an unreaped child here, so a reused pid is never signalled.
        if os.waitpid(pid, os.WNOHANG)[0] != pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not reap(pid, seconds):
                raise TimeoutError(f"zmx client {pid} was not reaped {seconds}s after SIGKILL")


def kill_daemon(zmx, name, env, seconds=REAP_SECONDS):
    """Kill one named daemon in env's ZMX_DIR and wait until neither the listing nor its pid remains."""
    def listed():
        listing = subprocess.run([zmx, "list"], env=env, capture_output=True, timeout=seconds)
        if listing.returncode != 0:
            raise AssertionError(f"zmx list failed: {listing!r}")
        for row in listing.stdout.decode().splitlines():
            fields = dict(cell.partition("=")[::2] for cell in row.strip().split("\t"))
            if fields.get("name") == name:
                return int(fields["pid"]) if fields.get("pid", "").isdigit() else -1
        return None

    pid = listed()
    started = started_at(pid) if pid is not None and pid > 0 else None
    # zmx kill exits 0 for an absent session as well, so the listing decides the outcome.
    result = subprocess.run([zmx, "kill", name, "--force"], env=env, capture_output=True, timeout=seconds)
    if result.returncode != 0:
        raise AssertionError(f"zmx kill {name} failed: {result!r}")
    deadline = time.monotonic() + seconds
    while listed() is not None or (started is not None and running(pid, started)):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"zmx daemon {name} (pid {pid}) still present {seconds}s after kill")
        time.sleep(0.05)


def started_at(pid):
    """The pid's start time as ps prints it, or None once it has exited; a zombie has exited."""
    state = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                           timeout=5)
    fields = state.stdout.split(None, 1)
    return None if len(fields) < 2 or fields[0].startswith("Z") else fields[1].strip()


def running(pid, started=None):
    """Read a non-child's state instead of signalling it; a reused pid has another start time."""
    now = started_at(pid)
    return now is not None and (started is None or now == started)


def read_until(fd, needle, seconds):
    out = b""
    deadline = time.monotonic() + seconds
    while needle not in out and (left := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


def cleanup_all(steps):
    """Run every cleanup step even when earlier ones fail; return the failures in order."""
    failures = []
    for step in steps:
        try:
            step()
        except Exception as error:  # every owned resource still gets its attempt
            failures.append(error)
    return failures


@unittest.skipUnless(os.access(ZMX, os.X_OK) and ZSH, "needs AGTERM_IDENTITY_TEST_ZMX and zsh")
class RealZmxPrefillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zp-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for leaf in ("s", "z", "h"):
            (root / leaf).mkdir()
        # An existing .zshrc keeps zsh-newuser-install from taking the typed input.
        (root / "z" / ".zshrc").write_text("PS1='READY> '\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root / "h"),
                    "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": ZSH,
                    "ZDOTDIR": str(root / "z"), "ZMX_DIR": str(root / "s")}
        self.name = "agterm-prefill-" + uuid.uuid4().hex[:8]
        self.addCleanup(kill_daemon, ZMX, self.name, self.env)

    def attach(self, initial):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execve(ZMX, [ZMX, "attach", self.name], self.env)
            finally:
                os._exit(127)
        self.addCleanup(close_client, pid, fd)
        os.write(fd, initial)  # immediately, as libghostty writes initial_input right after spawn
        return fd

    def test_prefilled_line_waits_for_enter(self):
        fd = self.attach(LINE)
        before = read_until(fd, RAN, 6)
        self.assertNotIn(RAN, before, before)
        self.assertIn(b"READY> ", before, "the shell never reached its prompt: " + repr(before))
        os.write(fd, b"\r")
        after = read_until(fd, RAN, 10)
        self.assertIn(RAN, after, "the pre-filled line did not reach the line editor: " + repr(before + after))

    def test_negative_arm_a_trailing_newline_runs_unattended(self):
        # The behaviour being removed: with a newline the command runs with no key pressed.
        fd = self.attach(LINE + b"\n")
        out = read_until(fd, RAN, 10)
        self.assertIn(RAN, out, out)


@unittest.skipUnless(os.access(ZMX, os.X_OK) and ZSH, "needs AGTERM_IDENTITY_TEST_ZMX and zsh")
class RealZmxCleanupControlTests(unittest.TestCase):
    """A client that cannot finish exiting must fail its cleanup on time and leave daemon teardown reachable."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zc-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for leaf in ("s", "o", "z", "h"):
            (root / leaf).mkdir()
        (root / "z" / ".zshrc").write_text("PS1='READY> '\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root / "h"),
                    "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": ZSH,
                    "ZDOTDIR": str(root / "z"), "ZMX_DIR": str(root / "s")}
        self.other_env = dict(self.env, ZMX_DIR=str(root / "o"))
        self.name = "agterm-cleanup-" + uuid.uuid4().hex[:8]
        self.sentinel = "agterm-sentinel-" + uuid.uuid4().hex[:8]

    def start_daemon(self, name, env):
        self.addCleanup(kill_daemon, ZMX, name, env)
        result = subprocess.run([ZMX, "run", name, "-d", "sleep", "300"], env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=REAP_SECONDS)
        self.assertEqual(result.returncode, 0, result)

    def listed(self, env):
        listing = subprocess.run([ZMX, "list", "--short"], env=env, capture_output=True, text=True,
                                 timeout=REAP_SECONDS)
        self.assertEqual(listing.returncode, 0, listing)
        return listing.stdout.split()

    def test_held_client_times_out_and_daemon_teardown_still_runs(self):
        self.start_daemon(self.sentinel, self.env)
        self.start_daemon(self.name, self.other_env)
        self.addCleanup(kill_daemon, ZMX, self.name, self.env)
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execve(ZMX, [ZMX, "attach", self.name], self.env)
            finally:
                os._exit(127)
        self.closed = False
        self.addCleanup(self.finish_client, pid, fd)
        os.write(fd, LINE + b"\n")
        self.assertIn(RAN, read_until(fd, RAN, 10))
        os.write(fd, b"print -r -- UNREAD\n")
        self.assertTrue(select.select([fd], [], [], 5)[0], "no output left pending on the master")
        # An owned holder keeps the master open and that output unread, so closing our copy cannot release it.
        holder = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"],
                                  stdin=subprocess.PIPE, pass_fds=(fd,))
        self.addCleanup(holder.wait, REAP_SECONDS)
        self.addCleanup(holder.stdin.close)
        started = time.monotonic()
        self.closed = True
        try:
            close_client(pid, fd, seconds=1)
            outcome = None
        except TimeoutError as error:
            outcome = error
        self.assertLess(time.monotonic() - started, 2.5)
        if sys.platform == "darwin":
            self.assertIsInstance(outcome, TimeoutError)
        kill_daemon(ZMX, self.name, self.env)
        self.assertNotIn(self.name, self.listed(self.env))
        self.assertIn(self.sentinel, self.listed(self.env))
        self.assertIn(self.name, self.listed(self.other_env))
        holder.stdin.close()
        self.assertEqual(holder.wait(REAP_SECONDS), 0)
        if outcome is not None:
            self.assertTrue(reap(pid, REAP_SECONDS), f"client {pid} survived its released master")

    def finish_client(self, pid, fd):
        if not self.closed:
            close_client(pid, fd)
            return
        try:
            if not reap(pid, REAP_SECONDS):
                raise TimeoutError(f"zmx client {pid} was not reaped after its holder exited")
        except ChildProcessError:
            pass  # the test body already reaped it


def production_plan(path):
    """Execute the Swift test's actual emitted seed, never reconstruct its attachment command."""
    plan = json.loads(Path(path).read_text())
    root = Path(plan['root'])
    for leaf in ('s', 'z', 'h', 'cwd with spaces'):
        (root / leaf).mkdir()
    trace = root / 'startup'
    env = dict(os.environ, **plan['environment'], HOME=str(root / 'h'), TERM='xterm-256color',
               ZMX_DIR=str(root / 's'), AGTERM_STARTUP_TRACE=str(trace),
               AGTERM_PREFILL_REPORT=str(root / 'executed'))
    for leaf in ('.zshenv', '.zprofile', '.zshrc', '.zlogin'):
        (root / 'z' / leaf).write_text("builtin print -r -- " + leaf + " >> \"$AGTERM_STARTUP_TRACE\"\n" +
                                     ("PS1='READY> '\n" if leaf == '.zshrc' else ''))
    with (root / 'z/.zshenv').open('a') as stream:
        stream.write('/usr/bin/env > "$AGTERM_STARTUP_TRACE.env"\n')
    clients = []

    def spawn(args, child_env=env):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(root / 'cwd with spaces')
                os.execve(args[0], args, child_env)
            finally:
                os._exit(127)
        clients.append((pid, fd))
        return fd

    def attach():
        fd = spawn(['/bin/sh', '-c', plan['command']])
        if plan['initialInput']:
            os.write(fd, plan['initialInput'].encode())
        return fd

    def release():
        steps = [lambda: kill_daemon(plan['zmx'], plan['name'], env)]
        while clients:
            steps.append(lambda owned=clients.pop(): close_client(*owned))
        return cleanup_all(steps)

    def cleanup():
        failures = release()
        if failures:
            raise failures[0]

    try:
        inventory = subprocess.run([plan['zmx'], 'list'], env=env, capture_output=True, timeout=5)
        assert inventory.returncode == 0, inventory.stderr
        assert plan['name'].encode() not in inventory.stdout
        # A competing creator wins after the Swift plan captured an absent inventory.
        recorder = root / 'recorder.py'
        recorder.write_text("import os,tty,pathlib\ntty.setraw(0)\n"
                            "pathlib.Path(" + repr(str(root / 'ready')) + ").touch()\n"
                            "os.write(1,b'RECORDER_READY')\n"
                            "with open(" + repr(str(root / 'received')) + ", 'wb', buffering=0) as f:\n"
                            " while True: f.write(os.read(0,4096))\n")
        spawn([plan['zmx'], 'attach', plan['name'], '/usr/bin/python3', str(recorder)])
        deadline = time.monotonic() + 5
        while not (root / 'ready').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (root / 'ready').exists(), 'competing recorder did not become ready'
        second = attach()
        assert b'RECORDER_READY' in read_until(second, b'RECORDER_READY', 5)
        read_until(second, b'IMPOSSIBLE_RECORDER_OUTPUT', 0.5)
        received = (root / 'received').read_bytes()
        assert received == b'', f'existing daemon received restored input: {received!r}'
        assert not trace.exists(), 'creation startup ran while attaching to an existing daemon'
        print('PASS competing creator: attached replay, zero restored bytes', flush=True)
        cleanup()

        fd = attach()
        before = read_until(fd, b'READY> ', 5)
        assert b'READY> ' in before, repr(before)
        read_until(fd, b'IMPOSSIBLE_OUTPUT', 0.25)
        assert not (root / 'executed').exists(), 'capture executed before Enter'
        assert trace.read_text().splitlines() == ['.zshenv', '.zprofile', '.zshrc', '.zlogin']
        child_env = (root / 'startup.env').read_text()
        assert 'AGTERM_LIVE_PREFILL=' not in child_env, child_env
        assert 'AGTERM_LIVE_INTEGRATION=' not in child_env, child_env
        os.write(fd, b'\r')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (root / 'executed').exists() and (root / 'executed').read_text().endswith(']'):
                break
            time.sleep(0.02)
        assert (root / 'executed').exists(), 'captured argv never executed after Enter'
        observed = json.loads((root / 'executed').read_text())
        assert observed == plan['expectedArguments'], f'argv changed: expected={plan["expectedArguments"]!r}, observed={observed!r}'
        os.write(fd, b"printf '\\nCONTRACT:%s:%s:%s\\n' \"$PWD\" \"$ZDOTDIR\" \"${AGTERM_LIVE_PREFILL+present}\"\r")
        contract = f"CONTRACT:{root / 'cwd with spaces'}:{root / 'z'}:\r\n".encode()
        output = read_until(fd, contract, 5)
        assert contract in output, repr(output)
        print(f'PASS new shell: exact argv={observed!r}, Enter required, startup order, cwd, ZDOTDIR, payload unexported/unset', flush=True)
        cleanup()
        trace.unlink()
        failed = spawn(['/bin/sh', '-c', plan['failedCommand']])
        error = read_until(failed, b'No such file', 5)
        assert b'No such file' in error or b'FileNotFound' in error, repr(error)
        assert not trace.exists(), 'failed shell creation ran user startup'
        print('PASS failed shell creation: diagnostic observed, no user startup', flush=True)
    except BaseException:
        for failure in release():  # the original failure stays the one raised
            print(f'cleanup also failed: {failure!r}', file=sys.stderr, flush=True)
        raise
    cleanup()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--production-plan':
        production_plan(sys.argv[2])
    else:
        unittest.main()
