#!/usr/bin/env python3
"""Bounded, owned-only cleanup of the real-zmx fixture's clients (#422); needs no zmx."""
import io
import json
import os
import pty
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_zmx_prefill import cleanup_all, close_client, production_plan, reap, running, started_at


def pty_child(code):
    pid, fd = pty.fork()
    if pid == 0:
        try:
            exec(code)
        finally:
            os._exit(0)
    return pid, fd


class UnrelatedSentinel:
    """A live process that is not this process's child: started through a shell that exits at once."""

    def __init__(self):
        self.read_end, self.write_end = os.pipe()
        script = f'{shlex.quote(sys.executable)} -I -c "import os; os.read({self.read_end}, 1)" >/dev/null 2>&1 & echo $!'
        launcher = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=5,
                                  pass_fds=(self.read_end,))
        os.close(self.read_end)
        self.pid = int(launcher.stdout)

    def stop(self):
        os.close(self.write_end)  # EOF ends it without signalling a pid we do not own
        deadline = time.monotonic() + 5
        while running(self.pid):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"sentinel {self.pid} did not exit")
            time.sleep(0.05)


class CloseClientTests(unittest.TestCase):
    def test_reap_deadline_returns_for_a_child_that_keeps_running(self):
        pid, fd = pty_child("import signal, time; signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)")
        started = time.monotonic()
        reaped = reap(pid, 0.3)
        elapsed = time.monotonic() - started
        close_client(pid, fd)
        self.assertFalse(reaped)
        self.assertLess(elapsed, 1.5)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_already_exited_child_is_reaped(self):
        pid, fd = pty_child("pass")
        deadline = time.monotonic() + 5
        while running(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        close_client(pid, fd)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_a_pid_with_another_start_time_is_not_the_same_process(self):
        pid, fd = pty_child("import time; time.sleep(60)")
        started = started_at(pid)
        reused = running(pid, "Thu Jan  1 00:00:00 1970")
        same = running(pid, started)
        close_client(pid, fd)
        self.assertIsNotNone(started)
        self.assertTrue(same)
        self.assertFalse(reused)

    def test_a_zombie_has_no_start_time(self):
        pid, fd = pty_child("pass")
        deadline = time.monotonic() + 5
        while started_at(pid) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        gone = started_at(pid)
        close_client(pid, fd)
        self.assertIsNone(gone)

    def test_non_child_pid_is_never_signalled_and_its_descriptor_still_closes(self):
        sentinel = UnrelatedSentinel()
        self.addCleanup(sentinel.stop)
        fd = os.open(os.devnull, os.O_RDONLY)
        with self.assertRaises(ChildProcessError):
            close_client(sentinel.pid, fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertTrue(running(sentinel.pid))


class CleanupContinuationTests(unittest.TestCase):
    def test_every_step_runs_after_a_failure_and_failures_are_returned(self):
        sentinel = UnrelatedSentinel()
        self.addCleanup(sentinel.stop)
        pid, fd = pty_child("import time; time.sleep(60)")
        ran = []
        failures = cleanup_all([lambda: close_client(sentinel.pid, os.open(os.devnull, os.O_RDONLY)),
                                lambda: ran.append("daemon"),
                                lambda: close_client(pid, fd)])
        self.assertEqual([type(failure) for failure in failures], [ChildProcessError])
        self.assertEqual(ran, ["daemon"])
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        self.assertTrue(running(sentinel.pid))

    def test_unittest_still_runs_daemon_cleanup_when_client_cleanup_times_out(self):
        ran = []

        class Fixture(unittest.TestCase):
            def runTest(self):
                self.addCleanup(ran.append, "daemon")  # registered first, as in RealZmxPrefillTests.setUp
                self.addCleanup(self.fail_client)

            def fail_client(self):
                ran.append("client")
                raise TimeoutError("client was not reaped")

        result = unittest.TestResult()
        Fixture().run(result)
        self.assertEqual(ran, ["client", "daemon"])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("TimeoutError: client was not reaped", result.errors[0][1])

    def test_production_plan_raises_its_original_failure_after_cleanup_fails_too(self):
        with tempfile.TemporaryDirectory(prefix="zx-") as directory:
            plan = Path(directory) / "plan.json"
            missing = str(Path(directory) / "missing-zmx")
            plan.write_text(json.dumps({"root": directory, "environment": {}, "zmx": missing, "name": "agterm-x"}))
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(FileNotFoundError) as raised:
                production_plan(plan)
        self.assertIn("missing-zmx", str(raised.exception))
        self.assertIn("cleanup also failed: FileNotFoundError", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
