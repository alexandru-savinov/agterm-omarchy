#!/usr/bin/env python3
"""Real zmx: a line typed into a newly created daemon's shell without a line terminator waits for Enter.

This mirrors the Linux app after a Live daemon was lost: a plain `zmx attach <name>` creates the daemon
with the login shell, and the captured command arrives as initial input with no newline. Set
AGTERM_IDENTITY_TEST_ZMX to a real zmx executable (and have zsh on PATH); the cases are skipped otherwise.
"""
import os
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


if __name__ == "__main__":
    unittest.main()
