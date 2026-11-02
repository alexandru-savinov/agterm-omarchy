"""Local synthetic validation only: never launches a frontend, zmx, compiler, or VM tool."""
import contextlib
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('native-run-template')
prepare = load('native-prepare-template')


def row(pid, parent=1, start=None, name='zsh', state='S', tagged=False, uid=None):
    return dict(pid=pid, parent=parent, start=str(start or pid * 10), name=name, state=state,
                uid=os.getuid() if uid is None else uid, tagged=tagged)


class ProcessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {1: row(1, name='agterm-linux'), 2: row(2, parent=1),
                         3: row(3, parent=2, name='claude'), 4: row(4, parent=999, name='unrelated')}

    def test_protects_shell_agent_and_all_descendants(self):
        protected = runner.protected_tree(self.baseline)
        self.assertEqual(set(protected), {1, 2, 3})
        current = copy.deepcopy(self.baseline)
        del current[3]
        self.assertFalse(runner.preservation(protected, current)['passed'])

    def test_zombie_frontend_is_not_preserved(self):
        current = copy.deepcopy(self.baseline)
        current[1]['state'] = 'Z'
        self.assertEqual(runner.preservation(runner.protected_tree(self.baseline), current)['lost'],
                         {'1': 'not live: Z'})

    def test_pid_reuse_is_not_preservation(self):
        current = copy.deepcopy(self.baseline)
        current[2]['start'] = 'reused'
        self.assertFalse(runner.preservation(runner.protected_tree(self.baseline), current)['passed'])

    def test_marked_reparented_daemon_is_owned(self):
        owned = runner.OwnedProcesses(self.baseline, os.getuid())
        orphan = row(20, parent=999, name='zmx', tagged=True)
        owned.observe({**self.baseline, 20: orphan})
        self.assertEqual(owned.remaining({20: orphan}), [orphan])

    def test_baseline_process_never_becomes_owned_even_if_tagged(self):
        owned = runner.OwnedProcesses(self.baseline, os.getuid())
        current = copy.deepcopy(self.baseline)
        current[2]['tagged'] = True
        owned.observe(current)
        self.assertEqual(owned.records, {})

    def test_lineage_is_tracked_but_recycled_parent_is_not(self):
        owned = runner.OwnedProcesses(self.baseline, os.getuid())
        parent = row(10)
        owned.add(parent)
        child = row(11, parent=10)
        owned.observe({10: parent, 11: child})
        self.assertIn(runner.key(child), owned.records)
        outsider = row(12, parent=10)
        recycled = row(10, start='different')
        owned.observe({10: recycled, 12: outsider})
        self.assertNotIn(runner.key(outsider), owned.records)

    def test_foreign_uid_is_not_owned(self):
        owned = runner.OwnedProcesses(self.baseline, os.getuid())
        owned.observe({20: row(20, tagged=True, uid=os.getuid() + 1)})
        self.assertFalse(owned.records)

    def test_pidfd_refuses_reused_identity_without_signal(self):
        original = row(55)
        with patch.object(runner.os, 'pidfd_open', return_value=90, create=True), \
             patch.object(runner, 'process_record', return_value=row(55, start='reused')), \
             patch.object(runner.signal, 'pidfd_send_signal', create=True) as send, \
             patch.object(runner.os, 'close') as close:
            self.assertFalse(runner.signal_owned(original, signal.SIGTERM))
            send.assert_not_called()
            close.assert_called_once_with(90)

    def test_pidfd_sends_only_to_the_opened_verified_handle(self):
        original = row(55)
        with patch.object(runner.os, 'pidfd_open', return_value=90, create=True), \
             patch.object(runner, 'process_record', return_value=original), \
             patch.object(runner.signal, 'pidfd_send_signal', create=True) as send, \
             patch.object(runner.os, 'close'):
            self.assertTrue(runner.signal_owned(original, signal.SIGTERM))
            send.assert_called_once_with(90, signal.SIGTERM)


class FilesystemSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_stage_refuses_replay_before_overwriting_evidence(self):
        stage = self.root / 'native-config-isolation.test'
        stage.mkdir(mode=0o700)
        runner.claim_stage(stage, self.root, os.getuid())
        original = (stage / 'run-attempt.json').read_bytes()
        with self.assertRaises(FileExistsError):
            runner.claim_stage(stage, self.root, os.getuid())
        self.assertEqual((stage / 'run-attempt.json').read_bytes(), original)

    def test_stage_refuses_symlink(self):
        real = self.root / 'real'
        real.mkdir(mode=0o700)
        link = self.root / 'native-config-isolation.link'
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'private owned'):
            runner.claim_stage(link, self.root, os.getuid())

    def test_stage_refuses_existing_payload_even_if_dangling_symlink(self):
        stage = self.root / 'native-config-isolation.test'
        stage.mkdir(mode=0o700)
        (stage / 'agterm-stage').symlink_to(self.root / 'absent')
        with self.assertRaisesRegex(RuntimeError, 'previously extracted'):
            runner.claim_stage(stage, self.root, os.getuid())

    def test_manifest_detects_permissions_symlink_targets_and_absence(self):
        payload = self.root / 'payload'
        payload.mkdir()
        binary = payload / 'binary'
        binary.write_text('content')
        (payload / 'link').symlink_to(binary.name)
        before = runner.manifest(payload)
        binary.chmod(0o700)
        self.assertNotEqual(before, runner.manifest(payload))
        binary.chmod(before['binary']['mode'])
        (payload / 'link').unlink()
        (payload / 'link').symlink_to('different')
        self.assertNotEqual(before, runner.manifest(payload))
        self.assertEqual(runner.manifest(self.root / 'missing'), {'.': None})

    def test_generated_launcher_isolates_runtime_and_retains_desktop_routes(self):
        stage = self.root / 'stage'
        stage.mkdir(mode=0o700)
        shared = self.root / 'shared'
        shared.mkdir()
        shared_config = shared / runner.RUNTIME_FILES[0]
        shared_config.write_text('real owner configuration')
        base = {'XDG_RUNTIME_DIR': str(shared), 'WAYLAND_DISPLAY': 'wayland-1',
                'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(shared / 'bus')}
        with patch.object(Path, 'is_socket', return_value=True):
            launcher, runtime, records = runner.make_launcher(stage, 'live', self.root / 'payload', base, 'test-token')
        original_base = dict(base)
        with tempfile.TemporaryDirectory(prefix='agt-wl-helpertest-', dir='/tmp') as temporary:
            run = Path(temporary)
            state = run / 'state'
            state.mkdir()
            env = dict(base, AGTERM_STATE_DIR=str(state), AGTERM_CONTROL_SOCKET=str(state / 'agterm.sock'),
                       OMARCHY_NATIVE_RUN_TOKEN='test-token')
            original_resolve = Path.resolve
            original_read = Path.read_text
            def resolve(path, *args, **kwargs):
                return path if str(path).startswith(str(run)) else original_resolve(path, *args, **kwargs)
            def read(path, *args, **kwargs):
                if str(path) == '/proc/self/stat':
                    return '55 (launcher) S 1 ' + '0 ' * 17 + '1234 0'
                return original_read(path, *args, **kwargs)
            def fake_exec(path, argv):
                self.assertEqual(os.environ['XDG_RUNTIME_DIR'], str(runtime))
                self.assertEqual(os.environ['WAYLAND_DISPLAY'], str(shared / 'wayland-1'))
                self.assertEqual(os.environ['DBUS_SESSION_BUS_ADDRESS'], base['DBUS_SESSION_BUS_ADDRESS'])
                (Path(os.environ['XDG_RUNTIME_DIR']) / runner.RUNTIME_FILES[0]).write_text('test config')
            with patch.dict(os.environ, env, clear=True), patch.object(Path, 'resolve', resolve), \
                 patch.object(Path, 'read_text', read), patch.object(os, 'execv', side_effect=fake_exec) as executed, \
                 patch.object(sys, 'argv', ['launcher']):
                exec(compile(launcher.read_text(), str(launcher), 'exec'), {'__name__': '__main__'})
            executed.assert_called_once()
            self.assertEqual(len(list(records.glob('*.json'))), 1)
        self.assertEqual(base, original_base)
        self.assertEqual(shared_config.read_text(), 'real owner configuration')
        self.assertEqual((runtime / runner.RUNTIME_FILES[0]).read_text(), 'test config')
        self.assertFalse(runtime.is_symlink())


class FailurePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def fake_run(self):
        protected = {1: row(1, name='agterm-linux'), 2: row(2, parent=1, name='claude')}
        with patch.object(runner, 'processes', return_value=protected), \
             patch.object(Path, 'is_socket', return_value=True):
            run = runner.ModeRun(self.root, 'live', self.root / 'payload',
                                 {'XDG_RUNTIME_DIR': str(self.root), 'WAYLAND_DISPLAY': 'wayland-1'},
                                 protected, runner.Interrupts())
        return run, protected

    def test_timeout_and_endpoint_failure_still_clean_detached_owned_processes_and_report(self):
        run, protected = self.fake_run()
        leader, detached = row(10, tagged=True, name='python3'), row(11, tagged=True, name='zmx')
        current = {**protected, 10: leader, 11: detached}
        fake = SimpleNamespace(pid=10, returncode=None)
        fake.poll = lambda: fake.returncode
        def collect():
            run.owned.observe(current)
            return current
        def send(process, sig):
            self.assertNotIn(process['pid'], protected)
            run.actions.append({'pid': process['pid'], 'signal': sig})
            if sig in (signal.SIGTERM, signal.SIGKILL):
                current.pop(process['pid'], None)
                if process['pid'] == 10:
                    fake.returncode = -int(sig)
        with patch.object(runner.subprocess, 'Popen', return_value=fake), \
             patch.object(runner, 'process_record', return_value=leader), \
             patch.object(run, 'collect', side_effect=collect), \
             patch.object(run, 'wait_until', side_effect=lambda *a, **kw: collect()), \
             patch.object(run, 'send', side_effect=send), \
             patch.object(run, 'endpoint_cleanup', side_effect=subprocess.TimeoutExpired('zmx', 3)), \
             patch.object(runner.time, 'monotonic', side_effect=[0, 241]):
            result = run.execute()
        self.assertIn('TimeoutError', result['error'])
        audit = result['independent_cleanup']
        self.assertTrue(audit['timed_out'])
        self.assertTrue(audit['forced_cleanup'])
        self.assertFalse(audit['passed'])
        self.assertEqual(audit['remaining_processes'], [])
        self.assertTrue(audit['preservation']['passed'])
        self.assertEqual(set(current), set(protected))
        self.assertTrue((self.root / 'live-recovery.json').exists())
        self.assertTrue(any('TimeoutExpired' in item for item in audit['errors']))

    def test_unexpected_collection_exception_still_runs_cleanup(self):
        run, _ = self.fake_run()
        leader = row(10, tagged=True)
        fake = SimpleNamespace(pid=10, returncode=None, poll=lambda: None)
        with patch.object(runner.subprocess, 'Popen', return_value=fake), \
             patch.object(runner, 'process_record', return_value=leader), \
             patch.object(run, 'collect', side_effect=ValueError('broken log')), \
             patch.object(run, 'finish') as finish:
            result = run.execute()
        finish.assert_called_once_with(True)
        self.assertIn('broken log', result['error'])

    def test_cleanup_audit_exception_is_retained_instead_of_success(self):
        run, _ = self.fake_run()
        with patch.object(runner.subprocess, 'Popen', side_effect=OSError('cannot launch')), \
             patch.object(run, 'finish', side_effect=RuntimeError('audit failed')):
            result = run.execute()
        self.assertFalse(result['passed'])
        self.assertIn('cannot launch', result['error'])
        self.assertIn('audit failed', result['cleanup_error'])
        self.assertTrue((self.root / 'live-recovery.json').exists())

    def test_cleanup_refuses_preexisting_identity(self):
        run, protected = self.fake_run()
        with patch.object(runner, 'signal_owned') as send:
            with self.assertRaisesRegex(RuntimeError, 'pre-existing'):
                run.send(protected[1], signal.SIGKILL)
            send.assert_not_called()

    def test_sigterm_is_catchable_and_repeated_signal_does_not_abort_cleanup(self):
        state = runner.Interrupts()
        with self.assertRaises(InterruptedError):
            state.handle(signal.SIGTERM, None)
        state.cleaning = True
        state.handle(signal.SIGTERM, None)
        self.assertEqual(state.received, [signal.SIGTERM, signal.SIGTERM])

    def test_preflight_failure_prints_failure_record_without_changing_user(self):
        output = io.StringIO()
        with patch.object(runner.pwd, 'getpwnam') as account, contextlib.redirect_stdout(output):
            code = runner.main({'source_commit': 'wrong', 'source_run': 1})
        account.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(report['report']['passed'])
        self.assertEqual(report['report']['phase'], 'failed')
        self.assertIsNone(report['stage'])

    def test_prepare_rejects_wrong_source_before_compilation_or_user_change(self):
        with patch.object(prepare.pwd, 'getpwnam') as account, \
             patch.object(prepare.subprocess, 'Popen') as launch, contextlib.redirect_stdout(io.StringIO()) as output:
            code = prepare.main({'source_commit': 'wrong', 'source_run': 1})
        self.assertEqual(code, 1)
        account.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['recovery']['native_test_started'])

    def test_failed_attempt_still_records_installed_payload_and_session_preservation(self):
        account = SimpleNamespace(pw_name='fixture-user', pw_dir=str(self.root), pw_uid=os.getuid(),
                                  pw_gid=os.getgid(), pw_shell='/bin/bash')
        parent = self.root / 'src/agterm-logs'
        parent.mkdir(parents=True)
        stage = parent / 'native-config-isolation.test'
        stage.mkdir(mode=0o700)
        (stage / 'preparation.json').write_text(json.dumps({'source_commit': 'wrong', 'source_run': runner.SOURCE_RUN}))
        installed = self.root / '.local/lib/agterm'
        installed.mkdir(parents=True)
        binary = installed / 'binary'
        binary.write_text('original')
        original_check = runner.checked_file
        def check(path, uid):
            binary.write_text('changed during failed attempt')
            return original_check(path, uid)
        protected = {1: row(1, name='agterm-linux'), 2: row(2, parent=1, name='claude')}
        final = copy.deepcopy(protected)
        final[2]['state'] = 'Z'
        config = {'source_commit': runner.SOURCE, 'source_run': runner.SOURCE_RUN, 'stage': str(stage)}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, dict(os.environ)))
            for name in ('initgroups', 'setgid', 'setuid', 'chdir', 'close'):
                stack.enter_context(patch.object(runner.os, name))
            stack.enter_context(patch.object(runner.os, 'uname', return_value=SimpleNamespace(sysname='Linux', machine='aarch64')))
            stack.enter_context(patch.object(runner.os, 'pidfd_open', return_value=901, create=True))
            stack.enter_context(patch.object(runner.signal, 'pidfd_send_signal', create=True))
            stack.enter_context(patch.object(runner.pwd, 'getpwnam', return_value=account))
            stack.enter_context(patch.object(runner.pwd, 'getpwuid', return_value=account))
            stack.enter_context(patch.object(runner, 'checked_file', side_effect=check))
            stack.enter_context(patch.object(runner, 'processes', side_effect=[protected, final]))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = runner.main(config)
        report = json.loads(output.getvalue())['report']
        self.assertEqual(code, 1)
        self.assertIn('Wrong preparation', report['error'])
        self.assertFalse(report['installed_payload_unchanged'])
        self.assertFalse(report['preexisting_sessions_preserved'])
        self.assertTrue(report['login_shell_unchanged'])
        self.assertEqual(json.loads((stage / 'report.json').read_text()), report)

    def test_preparation_compiler_timeout_retains_failure_and_targets_only_compiler_group(self):
        account = SimpleNamespace(pw_name='fixture-user', pw_dir=str(self.root), pw_uid=os.getuid(),
                                  pw_gid=os.getgid(), pw_shell='/bin/bash')
        (self.root / 'src/agterm-logs').mkdir(parents=True)
        fixture = b'synthetic asset bytes\n'
        fixture_sha = hashlib.sha256(fixture).hexdigest()
        config = {'source_commit': prepare.SOURCE, 'source_run': prepare.SOURCE_RUN,
                  'assets': {name: {'sha256': fixture_sha,
                                    'data': base64.b64encode(fixture).decode()}
                             for name in prepare.ASSETS}}
        compiler = SimpleNamespace(pid=900, poll=lambda: None)
        from unittest.mock import Mock
        compiler.wait = Mock(side_effect=[subprocess.TimeoutExpired('cc', 30), 0])
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(prepare.ASSETS, {name: fixture_sha for name in prepare.ASSETS}))
            stack.enter_context(patch.dict(os.environ, dict(os.environ)))
            for name in ('initgroups', 'setgid', 'setuid', 'chdir'):
                stack.enter_context(patch.object(prepare.os, name))
            stack.enter_context(patch.object(prepare.os, 'uname', return_value=SimpleNamespace(sysname='Linux', machine='aarch64')))
            stack.enter_context(patch.object(prepare.pwd, 'getpwnam', return_value=account))
            stack.enter_context(patch.object(prepare.pwd, 'getpwuid', return_value=account))
            stack.enter_context(patch.object(Path, 'is_file', return_value=True))
            stack.enter_context(patch.object(prepare.subprocess, 'Popen', return_value=compiler))
            kill = stack.enter_context(patch.object(prepare.os, 'killpg'))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = prepare.main(config)
        self.assertEqual(code, 1)
        kill.assert_called_once_with(900, signal.SIGKILL)
        report = json.loads(output.getvalue())
        self.assertIn('TimeoutExpired', report['error'])
        self.assertTrue(report['login_shell_unchanged'])
        self.assertTrue(report['recovery']['stage_retained'])
        self.assertFalse(report['recovery']['native_test_started'])
        self.assertEqual(json.loads((Path(report['stage']) / 'preparation-status.json').read_text()), report)


class AcceptanceTests(unittest.TestCase):
    def record(self, mode='none'):
        identity = {'harness': {'sha256': runner.ASSETS['harness.sh']}, 'run_id': 'same-run',
                    'app_processes': [{'sha256': 'app'}] * (2 if mode == 'live' else 1),
                    'binaries': {'zmx': {'sha256': 'zmx'}}, 'positive_control': {'passed': True}}
        return {'passed': False, 'returncode': 0,
                'result': {'passed': True, 'restore_mode': mode, 'checks': ['check'] * (9 if mode == 'live' else 7),
                           'identity': identity}, 'cleanup': {'passed': True, 'identity': copy.deepcopy(identity)},
                'independent_cleanup': {'passed': True, 'tracked_process_count': 4, 'endpoints': ['test-endpoint'],
                                        'shell_pids': {'shell-pid-before': 123, 'shell-pid-after': 123}}}

    def test_complete_ordinary_evidence_passes(self):
        record = self.record()
        runner.accept_mode(record, 'none', 'app', 'zmx')
        self.assertTrue(record['passed'])

    def test_mixed_run_identity_fails(self):
        record = self.record()
        record['cleanup']['identity']['run_id'] = 'other-run'
        with self.assertRaisesRegex(RuntimeError, 'run mismatch'):
            runner.accept_mode(record, 'none', 'app', 'zmx')

    def test_wrong_running_binary_fails(self):
        record = self.record()
        record['result']['identity']['app_processes'][0]['sha256'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'running app identity'):
            runner.accept_mode(record, 'none', 'app', 'zmx')

    def test_forced_cleanup_cannot_turn_a_failure_into_acceptance(self):
        record = self.record()
        record['independent_cleanup']['passed'] = False
        with self.assertRaisesRegex(RuntimeError, 'Harness/cleanup/preservation failed'):
            runner.accept_mode(record, 'none', 'app', 'zmx')

    def test_unequal_live_shell_pid_fails(self):
        record = self.record('live')
        record['independent_cleanup']['shell_pids']['shell-pid-after'] = 456
        with self.assertRaisesRegex(RuntimeError, 'unequal'):
            runner.accept_mode(record, 'live', 'app', 'zmx')

    def test_live_without_positive_control_fails(self):
        record = self.record('live')
        record['cleanup']['identity']['positive_control']['passed'] = False
        with patch.object(Path, 'exists', return_value=False), self.assertRaisesRegex(RuntimeError, 'sentinel'):
            runner.accept_mode(record, 'live', 'app', 'zmx')


if __name__ == '__main__':
    unittest.main(verbosity=2)
