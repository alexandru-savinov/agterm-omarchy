"""Exercise the runner's real process supervisor without a compiler or network."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'scripts/test-linux-ghostty-shutdown.sh'
# Execute the same inline supervisor as the standalone shell runner. Its CLI
# fixes the deadline at 90 seconds; the function seam allows a fast forced timeout.
SUPERVISOR = RUNNER.read_text().split("<<'PY_SUPERVISOR' &\n", 1)[1].split('\nPY_SUPERVISOR\n', 1)[0]
NAMESPACE = {'__name__': 'shutdown_supervisor_fixture'}
exec(compile(SUPERVISOR, str(RUNNER), 'exec'), NAMESPACE)


class ShutdownSupervisorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / 'fixture.zig'
        self.source.write_text('')
        self.zig = self.work / 'zig'
        self.zig.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, signal, sys, time
work = pathlib.Path(__file__).parent
config = json.loads((work / 'config.json').read_text())
(work / 'compiler.json').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'ppid': os.getppid(), 'args': sys.argv[1:]}))
assert '--test-no-exec' in sys.argv
if config.get('hang') == 'compiler':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
if config.get('compiler_exit'):
    sys.exit(config['compiler_exit'])
executable = pathlib.Path(next(arg.removeprefix('-femit-bin=') for arg in sys.argv if arg.startswith('-femit-bin=')))
executable.write_text(config['test_source'])
executable.chmod(0o755)
''')
        self.zig.chmod(0o755)
        self.test_source = f'#!{sys.executable}\nimport pathlib\nwork = pathlib.Path({str(self.work)!r})\n' + '''import json, os, signal, sys, time
config = json.loads((work / 'config.json').read_text())
(work / 'test.json').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'ppid': os.getppid()}))
if config.get('hang') == 'test':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
sys.exit(config.get('test_exit', 0))
'''
        self.configure()

    def configure(self, **values):
        (self.work / 'config.json').write_text(json.dumps({'test_source': self.test_source, **values}))

    def run_tests(self, timeout=0.6):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            status = NAMESPACE['run_tests'](str(self.zig), str(self.source), [], timeout=timeout)
        return status, errors.getvalue()

    def record(self, name):
        return json.loads((self.work / (name + '.json')).read_text())

    def wait_for_record(self, name, process):
        deadline = time.monotonic() + 5
        while True:
            self.assertIsNone(process.poll())
            self.assertLess(time.monotonic(), deadline)
            try:
                return self.record(name)
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.01)

    def assert_gone(self, record, direct=False):
        self.assertEqual(record['pid'], record['pgid'])
        self.assertNotEqual(record['pgid'], os.getpgrp())
        with self.assertRaises(ProcessLookupError):
            os.kill(record['pid'], 0)
        with self.assertRaises(ProcessLookupError):
            os.killpg(record['pgid'], 0)
        if direct:
            # A dead but unreaped direct child would still be returned here.
            with self.assertRaises(ChildProcessError):
                os.waitpid(record['pid'], os.WNOHANG)

    def test_forced_compiler_timeout_reaps_owned_process(self):
        self.configure(hang='compiler')
        status, errors = self.run_tests()
        self.assertEqual(status, 124)
        self.assertIn('shutdown regression timed out', errors)
        self.assert_gone(self.record('compiler'), direct=True)
        self.assertFalse((self.work / 'test.json').exists())

    def test_forced_test_timeout_reaps_test_directly(self):
        self.configure(hang='test')
        status, errors = self.run_tests()
        self.assertEqual(status, 124)
        self.assertIn('shutdown regression timed out', errors)
        for name in ('compiler', 'test'):
            record = self.record(name)
            self.assertEqual(record['ppid'], os.getpid())
            self.assert_gone(record, direct=True)

    def test_compiler_failure_does_not_execute_test(self):
        self.configure(compiler_exit=9)
        self.assertEqual(self.run_tests()[0], 9)
        self.assert_gone(self.record('compiler'), direct=True)
        self.assertFalse((self.work / 'test.json').exists())

    def test_test_failure_status_is_preserved(self):
        self.configure(test_exit=7)
        self.assertEqual(self.run_tests()[0], 7)
        self.assert_gone(self.record('compiler'), direct=True)
        self.assert_gone(self.record('test'), direct=True)

    def test_interruptions_reap_the_hung_test_before_supervisor_exits(self):
        self.configure(hang='test')
        supervisor = self.work / 'supervisor.py'
        supervisor.write_text(SUPERVISOR)
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                (self.work / 'test.json').unlink(missing_ok=True)
                process = subprocess.Popen([sys.executable, str(supervisor), str(self.zig), str(self.source)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    record = self.wait_for_record('test', process)
                    self.assertEqual(record['ppid'], process.pid)
                    process.send_signal(number)
                    _, errors = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + number)
                    self.assertIn(b'shutdown regression interrupted', errors)
                    self.assert_gone(self.record('compiler'))
                    self.assert_gone(record)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=5)

    def test_shell_interruption_reaps_test_before_deleting_work_directory(self):
        self.configure(hang='test')
        # Source preparation is irrelevant to this shell-lifetime check. Stub git
        # locally so the complete runner can execute without network or Zig; the
        # actual pinned source/queue acceptance remains the real runner's job.
        commands = self.work / 'bin'
        commands.mkdir()
        git = commands / 'git'
        git.write_text(f'#!{sys.executable}\n' + '''import io, sys, tarfile
args = sys.argv[1:]
if 'rev-parse' in args:
    print(args[-1].removesuffix('^{commit}'))
elif 'archive' in args:
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
        entry = tarfile.TarInfo('src/datastruct/blocking_queue.zig')
        entry.size = 0
        archive.addfile(entry, io.BytesIO())
elif 'apply' not in args:
    sys.exit(2)
''')
        git.chmod(0o755)
        # The normal shell version gate precedes the supervisor.
        text = self.zig.read_text()
        text = text.replace('work = pathlib.Path(__file__).parent',
                            "if sys.argv[1:] == ['version']:\n    print('0.16.0')\n    sys.exit(0)\nwork = pathlib.Path(__file__).parent")
        self.zig.write_text(text)
        environment = {**os.environ, 'ZIG': str(self.zig), 'PATH': str(commands) + os.pathsep + os.environ['PATH']}
        process = subprocess.Popen(['bash', str(RUNNER), str(self.work)], env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            record = self.wait_for_record('test', process)
            source = Path(self.record('compiler')['args'][2])
            generated_directory = source.parents[2]
            self.assertTrue(generated_directory.exists())
            # Send only to the shell, proving its EXIT path forwards termination
            # and waits for Python's cleanup before removing the private source.
            process.terminate()
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143)
            self.assert_gone(record)
            with self.assertRaises(ProcessLookupError):
                os.kill(record['ppid'], 0)
            self.assertFalse(generated_directory.exists())
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
