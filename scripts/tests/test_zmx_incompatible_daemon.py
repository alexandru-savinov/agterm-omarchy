#!/usr/bin/env python3
"""Real zmx: a daemon speaking another IPC protocol is reported, never attached to blind.

zmx v1 changed its message header, so a daemon a pre-v1 zmx started answers in frames a v1 client cannot
read. A stand-in daemon here answers every connection with one pre-v1 frame (tag, then a 32-bit length,
no protocol byte). Set AGTERM_IDENTITY_TEST_ZMX to a real zmx executable; the cases are skipped otherwise.
"""
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest


ZMX = os.environ.get("AGTERM_IDENTITY_TEST_ZMX", "")
NAME = "agterm-0123456789abcdef0123456789abcdef"


class PreV1Daemon:
    """Answers each connection with a pre-v1 Info frame and reads whatever the client sends."""

    def __init__(self, path):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(8)
        self.stopped = False
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stopped:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    conn.recv(4096)
                    # packed {tag: u8, len: u32}: Info (6), four payload bytes
                    conn.sendall(struct.pack("<BIxxx", 6, 4) + b"\0\0\0\0")
                    conn.recv(4096)
                except OSError:
                    pass

    def close(self):
        self.stopped = True
        self.server.close()


@unittest.skipUnless(os.access(ZMX, os.X_OK), "needs AGTERM_IDENTITY_TEST_ZMX")
class RealZmxIncompatibleDaemonTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="zi-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.daemon = PreV1Daemon(self.root / NAME)
        self.addCleanup(self.daemon.close)
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root),
                    "TERM": "xterm-256color", "ZMX_DIR": str(self.root)}

    def zmx(self, *args):
        return subprocess.run([ZMX, *args], env=self.env, capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL)

    def test_list_names_the_protocol_and_keeps_the_socket(self):
        listed = self.zmx("list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(f"name={NAME}\terr=IncompatibleProtocol\tstatus=unreachable", listed.stdout)
        # only a refused connection proves a socket stale; this daemon is alive
        self.assertTrue((self.root / NAME).is_socket())

    def test_attach_refuses_with_a_reason_instead_of_a_blank_screen(self):
        started = time.monotonic()
        attached = self.zmx("attach", NAME)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(attached.returncode, 1)
        self.assertIn("another IPC protocol", attached.stdout + attached.stderr)


if __name__ == "__main__":
    unittest.main()
