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
        self.addCleanup(self.kill_daemon)

    def kill_daemon(self):
        subprocess.run([ZMX, "kill", self.name, "--force"], env=self.env, capture_output=True, timeout=5)

    def attach(self, initial):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execve(ZMX, [ZMX, "attach", self.name], self.env)
            finally:
                os._exit(127)
        self.addCleanup(self.close_client, pid, fd)
        os.write(fd, initial)  # immediately, as libghostty writes initial_input right after spawn
        return fd

    @staticmethod
    def close_client(pid, fd):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(fd)

    @staticmethod
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

    def test_prefilled_line_waits_for_enter(self):
        fd = self.attach(LINE)
        before = self.read_until(fd, RAN, 6)
        self.assertNotIn(RAN, before, before)
        self.assertIn(b"READY> ", before, "the shell never reached its prompt: " + repr(before))
        os.write(fd, b"\r")
        after = self.read_until(fd, RAN, 10)
        self.assertIn(RAN, after, "the pre-filled line did not reach the line editor: " + repr(before + after))

    def test_negative_arm_a_trailing_newline_runs_unattended(self):
        # The behaviour being removed: with a newline the command runs with no key pressed.
        fd = self.attach(LINE + b"\n")
        out = self.read_until(fd, RAN, 10)
        self.assertIn(RAN, out, out)


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

    def cleanup():
        subprocess.run([plan['zmx'], 'kill', plan['name'], '--force'], env=env,
                       capture_output=True, timeout=5)
        while clients:
            RealZmxPrefillTests.close_client(*clients.pop())

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
        assert b'RECORDER_READY' in RealZmxPrefillTests.read_until(second, b'RECORDER_READY', 5)
        RealZmxPrefillTests.read_until(second, b'IMPOSSIBLE_RECORDER_OUTPUT', 0.5)
        received = (root / 'received').read_bytes()
        assert received == b'', f'existing daemon received restored input: {received!r}'
        assert not trace.exists(), 'creation startup ran while attaching to an existing daemon'
        print('PASS competing creator: attached replay, zero restored bytes', flush=True)
        cleanup()

        fd = attach()
        before = RealZmxPrefillTests.read_until(fd, b'READY> ', 5)
        assert b'READY> ' in before, repr(before)
        RealZmxPrefillTests.read_until(fd, b'IMPOSSIBLE_OUTPUT', 0.25)
        assert not (root / 'executed').exists(), 'capture executed before Enter'
        assert trace.read_text().splitlines() == ['.zshenv', '.zprofile', '.zshrc', '.zlogin']
        child_env = (root / 'startup.env').read_text()
        assert 'AGTERM_LIVE_PREFILL=' not in child_env, child_env
        assert 'AGTERM_LIVE_INTEGRATION=' not in child_env, child_env
        os.write(fd, b'\r')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (root / 'executed').exists() and (root / 'executed').read_text() == 'CREATION_BOUND_OK':
                break
            time.sleep(0.02)
        assert (root / 'executed').read_text() == 'CREATION_BOUND_OK'
        os.write(fd, b"printf '\\nCONTRACT:%s:%s:%s\\n' \"$PWD\" \"$ZDOTDIR\" \"${AGTERM_LIVE_PREFILL+present}\"\r")
        contract = f"CONTRACT:{root / 'cwd with spaces'}:{root / 'z'}:\r\n".encode()
        output = RealZmxPrefillTests.read_until(fd, contract, 5)
        assert contract in output, repr(output)
        print('PASS new shell: Enter required, startup order, cwd, ZDOTDIR, payload unexported/unset', flush=True)
        cleanup()
        trace.unlink()
        failed = spawn(['/bin/sh', '-c', plan['failedCommand']])
        error = RealZmxPrefillTests.read_until(failed, b'No such file', 5)
        assert b'No such file' in error or b'FileNotFound' in error, repr(error)
        assert not trace.exists(), 'failed shell creation ran user startup'
        print('PASS failed shell creation: diagnostic observed, no user startup', flush=True)
    finally:
        cleanup()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--production-plan':
        production_plan(sys.argv[2])
    else:
        unittest.main()
