#!/usr/bin/env python3
"""Real zmx: an attach client ended by a signal hands its terminal back.

The client makes stdin and stdout nonblocking and puts the terminal in raw mode. Both live on the open
file description it shares with whatever runs on that terminal next, so a client that dies holding them
leaves the next program's reads failing with EAGAIN. Set AGTERM_IDENTITY_TEST_ZMX to a real zmx executable;
the cases are skipped otherwise.
"""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import termios
import threading
import time
import unittest
import uuid


ZMX = os.environ.get("AGTERM_IDENTITY_TEST_ZMX", "")


@unittest.skipUnless(os.access(ZMX, os.X_OK), "needs AGTERM_IDENTITY_TEST_ZMX")
class RealZmxTtyRestoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zt-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "h").mkdir()
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root / "h"),
                    "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": "/bin/sh", "ZMX_DIR": str(root)}
        self.name = "agterm-tty-" + uuid.uuid4().hex[:8]
        self.addCleanup(self.kill_daemon)

    def kill_daemon(self):
        subprocess.run([ZMX, "kill", self.name, "--force"], env=self.env, capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=10)

    def attach(self, terminal_reads):
        """Attach on a fresh pty; the test keeps the slave fd, which shares the client's file description."""
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        before = termios.tcgetattr(slave)
        if terminal_reads:
            threading.Thread(target=self.read_until_closed, args=(master,), daemon=True).start()
        client = subprocess.Popen([ZMX, "attach", self.name], stdin=slave, stdout=slave, stderr=slave,
                                  env=self.env, start_new_session=True)
        self.addCleanup(self.reap, client)
        deadline = time.monotonic() + 10
        while not self.nonblocking(slave):
            self.assertIsNone(client.poll(), "the client exited before it attached")
            self.assertLess(time.monotonic(), deadline, "the client never made its terminal nonblocking")
            time.sleep(0.02)
        self.assertNotEqual(termios.tcgetattr(slave), before, "the client never entered raw mode")
        return client, slave, before

    @staticmethod
    def read_until_closed(master):
        while True:
            try:
                if not os.read(master, 65536):
                    return
            except OSError:
                return

    @staticmethod
    def reap(client):
        if client.poll() is None:
            client.kill()
        client.wait(timeout=10)

    @staticmethod
    def nonblocking(fd):
        return bool(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)

    def end_with(self, sig, terminal_reads):
        client, slave, before = self.attach(terminal_reads)
        client.send_signal(sig)
        try:
            status = client.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.fail(f"the client did not exit within 5 s of {sig.name}")
        return status, slave, before

    def test_a_terminated_client_restores_its_shared_terminal(self):
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=sig.name):
                status, slave, before = self.end_with(sig, terminal_reads=True)
                self.assertEqual(status, -sig, "the client must still die by the signal it was sent")
                self.assertFalse(self.nonblocking(slave), "O_NONBLOCK left on the shared terminal")
                self.assertEqual(termios.tcgetattr(slave), before, "raw mode left on the shared terminal")
                self.kill_daemon()

    def test_a_terminated_client_restores_a_terminal_that_stopped_reading(self):
        status, slave, before = self.end_with(signal.SIGTERM, terminal_reads=False)
        self.assertEqual(status, -signal.SIGTERM)
        self.assertFalse(self.nonblocking(slave), "O_NONBLOCK left on the shared terminal")
        self.assertEqual(termios.tcgetattr(slave), before, "raw mode left on the shared terminal")

    def test_a_killed_client_leaves_the_flag_for_the_host_to_clear(self):
        # SIGKILL runs no handler: the host has to reopen or reset the terminal itself (#429).
        status, slave, _ = self.end_with(signal.SIGKILL, terminal_reads=True)
        self.assertEqual(status, -signal.SIGKILL)
        self.assertTrue(self.nonblocking(slave))


if __name__ == "__main__":
    unittest.main()
