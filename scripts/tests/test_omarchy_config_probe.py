"""Disposable safety fixtures for the real probe helpers; no frontend, display or VM execution."""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'omarchy-config-probe.py'
SPEC = importlib.util.spec_from_file_location('omarchy_config_probe', SOURCE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def row(pid, *, parent=1, start='100', state='S', tagged=False, uid=None):
    return dict(pid=pid, parent=parent, start=start, state=state, tagged=tagged,
                uid=os.getuid() if uid is None else uid)


class FakeProc:
    """Only invented proc records; even permission failures never open real proc files."""
    def __init__(self, rows):
        self.rows, self.environments, self.environment_reads = rows, {}, []
        self.identity_reads, self.on_identity, self.on_environment = {}, None, None
        self.identity_errors, self.enumeration_error = {}, None

    @contextmanager
    def active(self):
        text, data, metadata, lstat, listing = Path.read_text, Path.read_bytes, Path.stat, Path.lstat, Path.iterdir
        def number(path):
            return int(path.parts[2]) if len(path.parts) > 2 and path.parts[1] == 'proc' else None
        def read_text(path, *args, **kwargs):
            pid = number(path)
            if pid is None: return text(path, *args, **kwargs)
            assert path.name == 'stat'
            self.identity_reads[pid] = self.identity_reads.get(pid, 0) + 1
            if self.on_identity: self.on_identity(pid, self.identity_reads[pid])
            if pid in self.identity_errors: raise self.identity_errors[pid]
            if pid not in self.rows: raise FileNotFoundError('synthetic process absent')
            value = self.rows[pid]
            fields = [value['state'], str(value['parent']), *(['0'] * 17), value['start']]
            return str(pid) + ' (inert fixture) ' + ' '.join(fields)
        def read_bytes(path, *args, **kwargs):
            pid = number(path)
            if pid is None: return data(path, *args, **kwargs)
            assert path.name == 'environ'; self.environment_reads.append(pid)
            if self.on_environment: self.on_environment(pid)
            value = self.environments.get(pid, b'OTHER=fixture\0')
            if isinstance(value, BaseException): raise value
            return value
        def info(path, *args, **kwargs):
            pid = number(path)
            if pid is None: return metadata(path, *args, **kwargs)
            if pid not in self.rows: raise FileNotFoundError('synthetic process absent')
            return SimpleNamespace(st_uid=self.rows[pid]['uid'])
        def entries(path):
            if path != Path('/proc'): return listing(path)
            if self.enumeration_error: raise self.enumeration_error
            return iter(Path('/proc') / str(pid) for pid in list(self.rows))
        with patch.object(Path, 'read_text', read_text), patch.object(Path, 'read_bytes', read_bytes), \
                patch.object(Path, 'stat', info), patch.object(Path, 'lstat', lambda path, *a, **k:
                    info(path, *a, **k) if number(path) is not None else lstat(path, *a, **k)), \
                patch.object(Path, 'iterdir', entries):
            yield self

    def observer(self, captured=()):
        instance = probe.Probe.__new__(probe.Probe)
        instance.token = 'fixture-token'
        instance.owned = probe.OwnedProcesses(probe.snapshot(strict=True), instance.token)
        instance.report = dict(process_absence_observations=[])
        for value in captured: instance.owned.add(value)
        self.identity_reads.clear()
        return instance


class ProcessAbsenceTests(unittest.TestCase):
    def test_unchanged_prelaunch_is_excluded_without_claiming_token_absence(self):
        for state in ('S', 'Z'):
            with self.subTest(state=state), FakeProc({10: row(10, state=state)}).active() as proc:
                proc.environments[10] = PermissionError('synthetic EACCES')
                instance = proc.observer([row(20)])
                instance.require_process_absence()
                proof = instance.report['process_absence_observations'][-1]
                self.assertTrue(proof['complete']); self.assertEqual(proc.environment_reads, [])
                self.assertEqual(proof['captured_pids'], proof['absent_captured_pids'])
                self.assertEqual(proof['captured_pids'], [20])
                excluded, = proof['excluded_prelaunch']
                self.assertEqual(excluded['token_observation'], 'excluded-unobserved-prelaunch')
                self.assertNotIn('tagged', excluded)
                self.assertEqual(probe.process_identity(excluded), instance.owned.baseline_processes[10])

    def test_baseline_copy_cannot_be_extended_or_changed_by_later_inventory(self):
        initial = {10: row(10)}
        owner = probe.OwnedProcesses(initial, 'fixture-token')
        initial[10]['start'] = '200'; initial[20] = row(20)
        self.assertEqual(owner.baseline_processes[10], (10, '100', os.getuid(), 1))
        self.assertNotIn(20, owner.baseline_processes)
        owner.observe({20: row(20, tagged=True)})
        self.assertNotIn(20, owner.baseline_processes)
        with self.assertRaises(TypeError): owner.baseline_processes[10] = (10, '200', os.getuid(), 1)
        with self.assertRaises(AttributeError): owner.baseline.add((20, '100', os.getuid()))

    def test_new_unreadable_token_candidate_remains_a_failure(self):
        with FakeProc({10: row(10)}).active() as proc:
            instance = proc.observer(); proc.rows[20] = row(20)
            proc.environments[20] = PermissionError('synthetic EACCES')
            with self.assertRaisesRegex(RuntimeError, 'Incomplete process token'): instance.require_process_absence()
            self.assertEqual(proc.environment_reads, [20])
            self.assertFalse(instance.report['process_absence_observations'][-1]['complete'])

    def test_baseline_birth_uid_and_parent_changes_refuse_even_readable_tokens(self):
        for field, value in (('start', '200'), ('uid', os.getuid() + 1), ('parent', 2)):
            with self.subTest(field=field), FakeProc({10: row(10)}).active() as proc:
                instance = proc.observer(); proc.rows[10][field] = value
                with self.assertRaisesRegex(RuntimeError, 'Prelaunch process identity changed'): instance.require_process_absence()
                self.assertEqual(proc.environment_reads, [])

    def test_baseline_identity_changes_during_exclusion_refuse(self):
        for field, value in (('start', '200'), ('uid', os.getuid() + 1), ('parent', 2)):
            with self.subTest(field=field), FakeProc({10: row(10)}).active() as proc:
                instance = proc.observer()
                proc.on_identity = lambda pid, count: proc.rows[pid].update({field: value}) if count == 2 else None
                with self.assertRaisesRegex(RuntimeError, 'identity changed during'): instance.require_process_absence()
                self.assertEqual(proc.environment_reads, [])

    def test_unreadable_or_incomplete_present_identity_never_qualifies_for_exclusion(self):
        for error in (PermissionError('synthetic stat denial'), ValueError('synthetic incomplete stat')):
            with self.subTest(error=error), FakeProc({10: row(10)}).active() as proc:
                instance = proc.observer(); proc.identity_errors[10] = error
                with self.assertRaisesRegex(RuntimeError, 'Incomplete process identity'): instance.require_process_absence()
                self.assertFalse(instance.report['process_absence_observations'][-1]['complete'])
        with FakeProc({10: row(10)}).active() as proc:
            instance = proc.observer(); proc.rows[10]['start'] = 'unknown'
            with self.assertRaisesRegex(RuntimeError, 'Incomplete process identity'): instance.require_process_absence()

    def test_unknown_new_zombie_cannot_claim_an_observed_token_absence(self):
        with FakeProc({}).active() as proc:
            instance = proc.observer(); proc.rows[20] = row(20, state='Z'); proc.environments[20] = b''
            with self.assertRaisesRegex(RuntimeError, 'Non-live new process'): instance.require_process_absence()
            self.assertEqual(proc.environment_reads, [])

    def test_complete_new_untagged_candidate_is_observed_and_new_tagged_candidate_is_owned(self):
        for tagged in (False, True):
            with self.subTest(tagged=tagged), FakeProc({}).active() as proc:
                instance = proc.observer(); proc.rows[20] = row(20)
                proc.environments[20] = (probe.TOKEN + '=fixture-token\0').encode() if tagged else b'OTHER=fixture\0'
                if tagged:
                    with self.assertRaisesRegex(RuntimeError, 'still exists or was reused'): instance.require_process_absence()
                    self.assertIn(probe.identity(proc.rows[20]), instance.owned.rows)
                else: instance.require_process_absence()
                proof = instance.report['process_absence_observations'][-1]
                self.assertEqual(proof['complete'], not tagged)
                self.assertEqual(proof['token_observations'][0]['tagged'], tagged)
                self.assertEqual(proof['excluded_prelaunch'], [])

    def test_new_candidate_identity_change_around_token_read_refuses(self):
        for field, value in (('start', '200'), ('uid', os.getuid() + 1), ('parent', 2)):
            with self.subTest(field=field), FakeProc({}).active() as proc:
                instance = proc.observer(); proc.rows[20] = row(20)
                proc.on_environment = lambda pid: proc.rows[pid].update({field: value})
                with self.assertRaisesRegex(RuntimeError, 'identity changed during'): instance.require_process_absence()

    def test_new_candidate_that_becomes_a_zombie_during_token_read_refuses(self):
        with FakeProc({}).active() as proc:
            instance = proc.observer(); proc.rows[20] = row(20)
            proc.on_environment = lambda pid: proc.rows[pid].update(state='Z')
            with self.assertRaisesRegex(RuntimeError, 'Non-live new process'): instance.require_process_absence()

    def test_token_read_failure_requires_actual_pid_absence(self):
        for disappears in (False, True):
            with self.subTest(disappears=disappears), FakeProc({}).active() as proc:
                instance = proc.observer([row(20)]); proc.rows[20] = row(20)
                proc.environments[20] = FileNotFoundError('synthetic environ disappeared')
                if disappears: proc.on_environment = lambda pid: proc.rows.pop(pid)
                if disappears: instance.require_process_absence()
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Incomplete process token'): instance.require_process_absence()
                self.assertEqual(instance.report['process_absence_observations'][-1]['complete'], disappears)

    def test_every_captured_pid_must_be_absent_including_reused_other_uid_and_zombie(self):
        for current in (row(20), row(20, start='200'), row(20, uid=os.getuid() + 1), row(20, state='Z')):
            with self.subTest(current=current), FakeProc({}).active() as proc:
                instance = proc.observer([row(20)]); proc.rows[20] = current
                with self.assertRaises(RuntimeError): instance.require_process_absence()
                self.assertFalse(instance.report['process_absence_observations'][-1]['complete'])

    def test_captured_pid_gets_no_baseline_exemption_on_its_direct_check(self):
        with FakeProc({10: row(10)}).active() as proc:
            instance = proc.observer(); instance.owned.rows[probe.identity(proc.rows[10])] = proc.rows[10]
            proc.environments[10] = PermissionError('synthetic captured PID denial')
            with self.assertRaisesRegex(RuntimeError, 'Incomplete process token'): instance.require_process_absence()
            self.assertEqual(proc.environment_reads, [10])

    def test_missing_baseline_and_incomplete_inventory_cannot_silently_exclude(self):
        with FakeProc({10: row(10)}).active() as proc:
            proc.environments[10] = PermissionError('synthetic denial')
            with self.assertRaisesRegex(RuntimeError, 'Incomplete process token'):
                probe.record(10, 'fixture-token', strict=True)
            instance = proc.observer(); proc.enumeration_error = PermissionError('synthetic enumeration denial')
            with self.assertRaises(PermissionError): instance.require_process_absence()
            self.assertFalse(instance.report['process_absence_observations'][-1]['complete'])


class ProcessOwnershipTests(unittest.TestCase):
    def test_adopts_only_new_tagged_processes_and_matching_descendants(self):
        baseline = {1: row(1), 8: row(8, tagged=True)}
        owner = probe.OwnedProcesses(baseline, 'test-token')
        current = {**baseline, 2: row(2, tagged=True), 3: row(3, parent=2), 4: row(4, parent=3),
                   9: row(9), 10: row(10, tagged=True, uid=os.getuid() + 1)}
        owner.observe(current)
        self.assertEqual({key[0] for key in owner.rows}, {2, 3, 4})
        # A different incarnation of PID2 is not an owned parent, so its new child cannot be adopted.
        owner.observe({2: row(2, start='reused'), 11: row(11, parent=2)})
        self.assertNotIn(11, {key[0] for key in owner.rows})

    def test_signal_refuses_unowned_and_preexisting_identities(self):
        original = row(10)
        owner = probe.OwnedProcesses({10: original}, 'token')
        owner.add(original)
        with patch.object(probe.os, 'pidfd_open', create=True) as opened:
            with self.assertRaisesRegex(RuntimeError, 'unowned'):
                owner.send(original, signal.SIGTERM)
            opened.assert_not_called()

    def test_signal_pid_reuse_is_a_noop_after_pidfd_open(self):
        original = row(20)
        owner = probe.OwnedProcesses({}, 'token'); owner.add(original)
        with patch.object(probe.os, 'pidfd_open', return_value=123, create=True), \
                patch.object(probe.os, 'close') as close, patch.object(probe, 'record', return_value=row(20, start='reused')), \
                patch.object(probe.signal, 'pidfd_send_signal', create=True) as send:
            self.assertFalse(owner.send(original, signal.SIGTERM))
            send.assert_not_called(); close.assert_called_once_with(123)

    def test_signal_uses_matching_pidfd_and_closes_it(self):
        original = row(20)
        owner = probe.OwnedProcesses({}, 'token'); owner.add(original)
        with patch.object(probe.os, 'pidfd_open', return_value=123, create=True), \
                patch.object(probe.os, 'close') as close, patch.object(probe, 'record', return_value=original), \
                patch.object(probe.signal, 'pidfd_send_signal', create=True) as send:
            self.assertTrue(owner.send(original, signal.SIGTERM))
            send.assert_called_once_with(123, signal.SIGTERM); close.assert_called_once_with(123)

    def test_remaining_excludes_zombies_unrelated_and_reused_pids(self):
        owner = probe.OwnedProcesses({}, 'token')
        owner.add(row(20)); owner.add(row(21)); owner.add(row(22))
        current = {20: row(20), 21: row(21, state='Z'), 22: row(22, start='reused'), 23: row(23)}
        self.assertEqual([entry['pid'] for entry in owner.remaining(current)], [20])


class ConfigSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.sentinel = self.root / 'unrelated-sentinel'; self.sentinel.write_bytes(b'preserve')

    def make_owner(self, name):
        owner = self.root / ('agterm-ghostty-' + name); owner.mkdir(mode=0o700)
        for theme in ('lightTheme', 'darkTheme'):
            target = owner / (theme + '.conf'); target.write_bytes(b''); target.chmod(0o600)
        return owner

    def test_snapshot_is_private_bounded_and_leaves_peer_and_sentinel_intact(self):
        first, second = self.make_owner('first'), self.make_owner('second')
        before = probe.configs(self.root, 2)
        self.assertEqual(len(before), 2)
        for path in first.iterdir():
            path.unlink()
        first.rmdir()
        after = probe.configs(self.root, 2)
        self.assertEqual(after, {second.name: before[second.name]})
        self.assertEqual(self.sentinel.read_bytes(), b'preserve')

    def test_growth_is_rejected(self):
        owner = self.make_owner('growing')
        (owner / 'retained-runtime.conf').write_bytes(b'old config')
        (owner / 'retained-runtime.conf').chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'bound exceeded'):
            probe.configs(self.root, 2)

    def test_directory_and_file_symlinks_are_not_followed(self):
        separate = tempfile.TemporaryDirectory(); self.addCleanup(separate.cleanup)
        outside = Path(separate.name).resolve()
        secret = outside / 'keep'; secret.write_bytes(b'preserve')
        link = self.root / 'agterm-ghostty-link'; link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            probe.configs(self.root, 2)
        link.unlink()
        owner = self.make_owner('file-link')
        (owner / 'lightTheme.conf').unlink()
        (owner / 'lightTheme.conf').symlink_to(outside / 'keep')
        with self.assertRaises(RuntimeError):
            probe.configs(self.root, 2)
        self.assertEqual((outside / 'keep').read_bytes(), b'preserve')

    def test_nonprivate_generated_file_is_rejected(self):
        owner = self.make_owner('public'); (owner / 'lightTheme.conf').chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'private regular'):
            probe.configs(self.root, 2)


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.payload = self.root / 'payload'; (self.payload / 'bin').mkdir(parents=True)
        data = b'fixture app bytes, never executed'
        (self.payload / 'bin/agterm-linux.bin').write_bytes(data)
        self.digest = hashlib.sha256(data).hexdigest()
        for name in ('agterm-linux', 'agtermctl'):
            path = self.payload / 'bin' / name; path.write_bytes(b'fixture only'); path.chmod(0o700)
        # A file plus a shape fake is sufficient; never bind a real display/control socket.
        (self.root / 'wayland').write_bytes(b'fake endpoint, never contacted')
        self.env = dict(PATH='/usr/bin:/bin', WAYLAND_DISPLAY='wayland', XDG_RUNTIME_DIR=str(self.root),
                        DBUS_SESSION_BUS_ADDRESS='unix:path=/fixture/no-bus')
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(probe.os, 'uname', return_value=SimpleNamespace(sysname='Linux')))
        self.stack.enter_context(patch.object(probe.os, 'pidfd_open', create=True))
        self.stack.enter_context(patch.object(probe.signal, 'pidfd_send_signal', create=True))
        self.stack.enter_context(patch.object(probe, 'snapshot', return_value={}))
        self.stack.enter_context(patch.object(probe.signal, 'signal'))
        self.stack.enter_context(patch.object(probe.pwd, 'getpwuid', return_value=SimpleNamespace(pw_shell='/bin/bash')))
        self.stack.enter_context(patch.object(Path, 'is_socket', lambda path: path == self.root / 'wayland'))

    def run_probe(self, **options):
        return probe.run_probe(self.payload, self.root / 'output', self.env, self.digest, **options)


class RunSafetyTests(RunFixture):
    def test_incomplete_prelaunch_inventory_refuses_before_any_frontend_launch(self):
        with patch.object(probe, 'snapshot', side_effect=RuntimeError('synthetic baseline gap')) as snapshot, \
                patch.object(probe.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'synthetic baseline gap'): self.run_probe()
            snapshot.assert_called_once_with(strict=True); popen.assert_not_called()

    def test_wrong_digest_rejected_before_output_or_launch(self):
        with patch.object(probe.subprocess, 'Popen') as popen, self.assertRaisesRegex(RuntimeError, 'digest mismatch'):
            probe.run_probe(self.payload, self.root / 'output', self.env, '0' * 64)
        popen.assert_not_called(); self.assertFalse((self.root / 'output').exists())

    def test_existing_output_is_never_reused_or_rewritten(self):
        output = self.root / 'output'; output.mkdir(); old = output / 'report.json'; old.write_bytes(b'old evidence')
        with patch.object(probe.subprocess, 'Popen') as popen, self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.run_probe()
        popen.assert_not_called(); self.assertEqual(old.read_bytes(), b'old evidence')

    def test_changed_bound_cannot_silently_weaken_the_gate(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly two'):
            self.run_probe(retained_file_limit=100)
        self.assertFalse((self.root / 'output').exists())

    def test_launch_failure_persists_failure_and_removes_only_socket_directory(self):
        environment = dict(os.environ)
        with patch.object(probe.subprocess, 'Popen', side_effect=OSError('fixture launch failure')):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertIn('fixture launch failure', '\n'.join(result['errors']))
        self.assertTrue(result['socket_directory_removed'])
        self.assertTrue(result['sentinel_preserved'])
        self.assertEqual(dict(os.environ), environment)
        self.assertEqual(json.loads((self.root / 'output/report.json').read_text()), result)

    def recovery_case(self, error, kill_fails=False):
        unrelated, owned = row(70), row(71, tagged=True)
        current = {70: unrelated}
        def exercise(instance):
            current[71] = owned; instance.owned.add(owned)
            raise error
        actions = []
        def send(instance, target, sig):
            self.assertEqual(target['pid'], 71)
            self.assertIn(probe.identity(target), instance.rows)
            actions.append(int(sig))
            if not kill_fails:
                current.pop(71, None)
            return True
        limits = dict(probe.LIMITS, recovery=0.001, drain=0)
        with patch.object(probe, 'snapshot', side_effect=lambda token=None, **kwargs: dict(current)), \
                patch.object(probe.Probe, 'exercise', exercise), patch.object(probe.OwnedProcesses, 'send', send), \
                patch.object(probe, 'LIMITS', limits), patch.object(probe.time, 'sleep'):
            result = self.run_probe()
        self.assertIn(70, current)
        self.assertFalse(result['passed'])
        self.assertTrue(result['forced_recovery'])
        self.assertTrue(result['sentinel_preserved'])
        self.assertTrue(actions)
        return result

    def test_timeout_cleans_only_owned_identity_and_still_fails(self):
        result = self.recovery_case(TimeoutError('fixture timeout'))
        self.assertFalse(result['remaining_processes'])
        self.assertIn('TimeoutError', '\n'.join(result['errors']))

    def test_interruption_runs_recovery_and_still_fails(self):
        result = self.recovery_case(KeyboardInterrupt())
        self.assertFalse(result['remaining_processes'])
        self.assertIn('KeyboardInterrupt', '\n'.join(result['errors']))

    def test_failed_recovery_preserves_a_failure_and_remaining_identity(self):
        result = self.recovery_case(TimeoutError('fixture timeout'), kill_fails=True)
        self.assertEqual([r['pid'] for r in result['remaining_processes']], [71])

    def test_cleanup_does_not_remove_an_unrelated_socket_directory_entry(self):
        kept = []
        def exercise(instance):
            instance.sockets = self.root / 'sockets'; instance.sockets.mkdir(mode=0o700)
            instance.socket_identity = probe.private_directory(instance.sockets)
            instance.control_files = probe.ControlFiles(instance.sockets, instance.deadline)
            foreign = instance.sockets / 'foreign'; foreign.write_bytes(b'keep me'); kept.append(foreign)
            raise RuntimeError('fixture failure')
        with patch.object(probe.Probe, 'exercise', exercise):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertEqual(kept[0].read_bytes(), b'keep me')
        self.assertIn('cleanup:', '\n'.join(result['errors']))

    def test_launch_keeps_outer_marker_and_routes_only_the_child_to_private_runtime(self):
        self.env['OMARCHY_NATIVE_RUN_TOKEN'] = 'outer-fixture-marker'
        self.env['LD_PRELOAD'] = '/fixture/must-not-be-loaded'
        original = dict(self.env)
        observed = []
        def launch(argv, **kwargs):
            env = kwargs['env']; observed.append(dict(env))
            self.assertEqual(env['OMARCHY_NATIVE_RUN_TOKEN'], 'outer-fixture-marker')
            self.assertNotIn('LD_PRELOAD', env)
            self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], self.env['DBUS_SESSION_BUS_ADDRESS'])
            self.assertEqual(env['WAYLAND_DISPLAY'], str(self.root / 'wayland'))
            self.assertEqual(env['XDG_RUNTIME_DIR'], str(self.root / 'output/shared-runtime'))
            self.assertTrue(env['AGTERM_CONTROL_SOCKET'].startswith('/tmp/agt-cfg-'))
            self.assertLess(len(env['AGTERM_CONTROL_SOCKET'].encode()), 100)
            raise OSError('stop before any frontend')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.env, original)

    def test_partial_success_cannot_pass_without_both_apps_and_all_checks(self):
        with patch.object(probe.Probe, 'exercise', return_value=None):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertTrue(result['sentinel_preserved'])



class RuntimeInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'unrelated-sentinel').write_bytes(b'keep')
        self.deadline = probe.time.monotonic() + 30
        with probe.opened_directory(self.root, None, self.deadline) as (_, parent): self.parent = parent
        self.peers = {}

    def peer(self, data=b'\0\0'):
        directory = self.root / 'dconf'; directory.mkdir(mode=0o700)
        user = directory / 'user'; user.write_bytes(data); user.chmod(0o600)
        return user

    def observe(self):
        return probe.runtime_inventory(self.root, 2, self.parent, self.peers, self.deadline)

    def test_original_peer_reproduction_and_partition_preserve_all_entries(self):
        user = self.peer()
        generated = self.root / 'agterm-ghostty-fixture'; generated.mkdir(mode=0o700)
        for name in ('light.conf', 'dark.conf'):
            file = generated / name; file.touch(mode=0o600)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected shared-runtime entry'):
            probe.configs(self.root, 2)
        result = self.observe()
        self.assertTrue(result['complete'], result)
        self.assertEqual(list(result['generated']), [generated.name])
        self.assertEqual(list(result['peers']), ['dconf'])
        self.assertEqual(probe.directory_key(result['parent']), probe.directory_key(self.parent))
        self.assertEqual(user.read_bytes(), b'\0\0')
        self.assertEqual((self.root / 'unrelated-sentinel').read_bytes(), b'keep')

    def test_timestamp_refresh_is_recorded_only_between_stable_observations(self):
        user = self.peer(); before = self.observe()
        old = user.stat(); os.utime(user, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
        after = self.observe()
        self.assertTrue(before['complete'] and after['complete'], after)
        refresh = after['peer_refreshes'][0]
        self.assertEqual(refresh['before'], before['peers']['dconf'])
        self.assertEqual(refresh['after'], after['peers']['dconf'])
        self.assertNotEqual(refresh['before']['user']['mtime_ns'], refresh['after']['user']['mtime_ns'])
        self.assertEqual(probe.peer_identity(refresh['before']), probe.peer_identity(refresh['after']))

    def test_source_flag_one_is_valid_initially_but_changed_bytes_are_a_gap(self):
        user = self.peer(b'\1\0'); self.assertTrue(self.observe()['complete'])
        user.write_bytes(b'\0\0')
        result = self.observe()
        self.assertFalse(result['complete'])
        self.assertIn('identity or bytes changed', str(result['peer_gaps']))
        self.assertEqual(user.read_bytes(), b'\0\0')

    def test_timestamp_change_during_read_is_not_a_refresh(self):
        user = self.peer(); original = os.read
        def changed(fd, size):
            data = original(fd, size)
            old = user.stat(); os.utime(user, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
            return data
        with patch.object(probe.os, 'read', side_effect=changed): result = self.observe()
        self.assertFalse(result['complete']); self.assertFalse(result['peer_refreshes'])
        self.assertIn('changed during read', str(result['gaps'])); self.assertEqual(self.peers, {})

    def test_replacement_and_disappearance_cannot_reset_peer_baseline(self):
        user = self.peer(); original = self.observe()['peers']['dconf']
        replacement = self.root / 'replacement'; replacement.write_bytes(b'\0\0'); replacement.chmod(0o600)
        os.replace(replacement, user)
        self.assertIn('identity or bytes changed', str(self.observe()['gaps']))
        self.assertEqual(self.peers['dconf'], original)
        user.unlink(); user.parent.rmdir()
        self.assertIn('disappeared', str(self.observe()['gaps']))
        self.assertEqual(self.peers['dconf'], original)

    def test_unknown_and_extra_peer_names_are_retained_with_gaps(self):
        unknown = self.root / 'unknown'; unknown.write_bytes(b'keep me')
        result = self.observe()
        self.assertFalse(result['complete']); self.assertIn('Unknown runtime peer', str(result['gaps']))
        self.assertIn('entry', result['peers']['unknown']); self.assertEqual(unknown.read_bytes(), b'keep me')
        unknown.unlink(); user = self.peer(); (user.parent / 'other').write_bytes(b'keep too')
        result = self.observe()
        self.assertIn('Unknown dconf peer entry', str(result['gaps']))
        self.assertEqual((user.parent / 'other').read_bytes(), b'keep too')

    def test_peer_bad_shapes_are_gaps_without_deletion(self):
        for data in (b'', b'\0', b'\0\0\0', b'xx', b'\0\1'):
            with self.subTest(data=data):
                user = self.peer(data)
                result = self.observe(); self.assertFalse(result['complete'], result)
                self.assertEqual(user.read_bytes(), data)
                user.unlink(); user.parent.rmdir()
        user = self.peer(); user.chmod(0o644)
        self.assertFalse(self.observe()['complete']); user.chmod(0o600)
        user.parent.chmod(0o755); self.assertFalse(self.observe()['complete']); user.parent.chmod(0o700)
        target = self.root / 'alias'; os.link(user, target)
        self.assertIn('link count', str(self.observe()['gaps'])); target.unlink()
        user.unlink(); user.symlink_to(self.root / 'unrelated-sentinel')
        self.assertFalse(self.observe()['complete']); self.assertTrue(user.is_symlink())

    def test_directory_alias_wrong_uid_and_incomplete_read_are_gaps(self):
        user = self.peer(); directory = user.parent
        saved = self.root / 'saved'; directory.rename(saved); directory.symlink_to(saved, target_is_directory=True)
        self.assertFalse(self.observe()['complete']); directory.unlink(); saved.rename(directory)
        original = probe.entry_at
        def wrong_uid(fd, name):
            result = original(fd, name)
            if name == 'user': result['uid'] += 1
            return result
        with patch.object(probe, 'entry_at', side_effect=wrong_uid):
            self.assertFalse(self.observe()['complete'])
        with patch.object(probe.os, 'read', side_effect=PermissionError('fixture unreadable indicator')):
            result = self.observe()
        self.assertIn('fixture unreadable indicator', str(result['gaps']))
        self.assertIn('user', result['peers']['dconf'])
        with patch.object(probe.os, 'read', return_value=b'\0'):
            self.assertFalse(self.observe()['complete'])
        self.assertEqual(user.read_bytes(), b'\0\0')

    def test_parent_replacement_alias_inventory_bound_and_deadline_fail_closed(self):
        original = self.parent; self.parent = dict(original, ino=original['ino'] + 1)
        self.assertIn('parent identity changed', str(self.observe()['gaps']))
        self.parent = original
        for number in range(64): (self.root / ('unknown-' + str(number))).touch()
        self.assertIn('entry bound exceeded', str(self.observe()['gaps']))
        with patch.object(probe.time, 'monotonic', return_value=self.deadline):
            self.assertIn('deadline exhausted', str(self.observe()['gaps']))

    def test_late_read_and_failed_final_parent_check_do_not_adopt_peer(self):
        self.peer(); now = [1.0]; self.deadline = 2.0; original = os.read
        def late(fd, size):
            data = original(fd, size); now[0] = 2.0; return data
        with patch.object(probe.time, 'monotonic', side_effect=lambda: now[0]), patch.object(probe.os, 'read', side_effect=late):
            self.assertFalse(self.observe()['complete'])
        self.assertEqual(self.peers, {})
        self.deadline = probe.time.monotonic() + 30
        checked = probe.check_directory; count = 0
        def failed_last(*args):
            nonlocal count
            count += 1
            if count == 3: raise RuntimeError('fixture final parent failure')
            return checked(*args)
        with patch.object(probe, 'check_directory', side_effect=failed_last): result = self.observe()
        self.assertIn('fixture final parent failure', str(result['gaps'])); self.assertEqual(self.peers, {})

    def test_generated_file_bound_remains_strict_with_a_valid_peer(self):
        self.peer(); directory = self.root / 'agterm-ghostty-extra'; directory.mkdir(mode=0o700)
        for number in range(3): (directory / str(number)).touch(mode=0o600)
        result = self.observe()
        self.assertFalse(result['complete']); self.assertIn('Retained config file bound', str(result['gaps']))
        self.assertFalse(result['peer_gaps'])


class ControlCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve(); self.root = self.base / 'sockets'; self.root.mkdir(mode=0o700)
        self.deadline = probe.time.monotonic() + 30
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.flock = self.stack.enter_context(patch.object(probe.fcntl, 'flock'))
        original = probe.entry_at
        def socket_shape(fd, name):
            result = original(fd, name)
            if result and name in ('left.sock', 'right.sock') and stat.S_ISREG(result['mode']):
                result['mode'] = stat.S_IFSOCK | stat.S_IMODE(result['mode'])
            return result
        self.stack.enter_context(patch.object(probe, 'entry_at', side_effect=socket_shape))
        self.files = probe.ControlFiles(self.root, self.deadline)
        self.actions, self.proof, self.absence = [], {}, 0

    def capture(self, name='left.sock'):
        for suffix in ('', '.lock'): (self.root / (name + suffix)).touch(mode=0o600)
        checks = []
        result = self.files.capture(name, lambda: checks.append(True), self.deadline)
        self.assertEqual(len(checks), 2)
        return result

    def absent(self):
        self.absence += 1

    def cleanup(self):
        self.files.cleanup(self.absent, self.deadline, self.actions, self.proof)

    def test_original_regular_lock_failure_and_all_locks_before_first_unlink(self):
        self.capture(); self.capture('right.sock')
        # The original rmdir-only cleanup demonstrably fails on retained regular locks.
        (self.root / 'left.sock').unlink(); (self.root / 'right.sock').unlink()
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['left.sock.lock', 'right.sock.lock'])
        with self.assertRaises(OSError): self.root.rmdir()
        acquired, events = [], []; original = os.unlink
        self.flock.side_effect = lambda fd, flags: (acquired.append(fd), events.append(('lock', flags)))
        def unlink(name, **kwargs):
            self.assertEqual(len(acquired), 2)
            self.assertTrue(all(probe.node(os.fstat(fd))['ino'] for fd in acquired))
            events.append(('unlink', name)); return original(name, **kwargs)
        with patch.object(probe.os, 'unlink', side_effect=unlink): self.cleanup()
        self.assertTrue(self.proof['complete']); self.assertFalse(self.root.exists())
        self.assertEqual(len(self.actions), 2); self.assertGreater(self.proof['absence_checks'], 4)
        self.assertEqual([event[0] for event in events[:2]], ['lock', 'lock'])
        self.assertTrue(all(event[1] == probe.fcntl.LOCK_EX | probe.fcntl.LOCK_NB for event in events[:2]))
        for fd in acquired:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_captured_unchanged_sockets_and_locks_are_the_only_removal_targets(self):
        self.capture(); self.capture('right.sock')
        self.cleanup()
        self.assertTrue(self.proof['complete'])
        self.assertEqual([action['name'] for action in self.actions],
                         ['left.sock', 'right.sock', 'left.sock.lock', 'right.sock.lock'])

    def test_stable_prelaunch_permission_boundary_allows_exact_own_lock_cleanup(self):
        self.capture(); self.capture('right.sock')
        (self.root / 'left.sock').unlink(); (self.root / 'right.sock').unlink()
        with FakeProc({10: row(10)}).active() as proc:
            proc.environments[10] = PermissionError('synthetic prelaunch EACCES')
            instance = proc.observer([row(20), row(21)])
            self.files.cleanup(instance.require_process_absence, self.deadline, self.actions, self.proof)
            self.assertTrue(self.proof['complete']); self.assertFalse(self.root.exists())
            self.assertEqual(self.proof['locks_acquired'], ['left.sock.lock', 'right.sock.lock'])
            self.assertEqual([action['name'] for action in self.actions], ['left.sock.lock', 'right.sock.lock'])
            self.assertEqual(proc.environment_reads, [])
            observations = instance.report['process_absence_observations']
            self.assertEqual(len(observations), self.proof['absence_checks'])
            for proof in observations:
                self.assertTrue(proof['complete'])
                self.assertEqual(proof['captured_pids'], [20, 21])
                self.assertEqual(proof['absent_captured_pids'], [20, 21])
                self.assertEqual([entry['pid'] for entry in proof['excluded_prelaunch']], [10])
                self.assertNotIn('tagged', proof['excluded_prelaunch'][0])

    def test_unreadable_new_candidate_refuses_before_any_lock_or_file_removal(self):
        self.capture(); self.capture('right.sock'); names = sorted(p.name for p in self.root.iterdir())
        with FakeProc({10: row(10)}).active() as proc:
            instance = proc.observer(); proc.rows[20] = row(20)
            proc.environments[20] = PermissionError('synthetic new candidate EACCES')
            with self.assertRaisesRegex(RuntimeError, 'Incomplete process token'):
                self.files.cleanup(instance.require_process_absence, self.deadline, self.actions, self.proof)
            self.assertFalse(instance.report['process_absence_observations'][-1]['complete'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), names)
        self.flock.assert_not_called(); self.assertEqual(self.actions, []); self.assertFalse(self.proof['complete'])

    def test_baseline_change_after_first_lock_preserves_files_and_releases_descriptor(self):
        self.capture(); self.capture('right.sock'); descriptors = []
        with FakeProc({10: row(10)}).active() as proc:
            instance = proc.observer()
            def lock(fd, flags):
                descriptors.append(fd); proc.rows[10]['parent'] = 2
            self.flock.side_effect = lock
            with self.assertRaisesRegex(RuntimeError, 'Prelaunch process identity changed'):
                self.files.cleanup(instance.require_process_absence, self.deadline, self.actions, self.proof)
            self.assertFalse(instance.report['process_absence_observations'][-1]['complete'])
        self.assertEqual(self.actions, []); self.assertEqual(len(descriptors), 1)
        for fd in descriptors:
            with self.assertRaises(OSError): os.fstat(fd)
        self.assertEqual(len(list(self.root.iterdir())), 4)

    def test_busy_second_lock_preserves_every_name_and_releases_first_descriptor(self):
        self.capture(); self.capture('right.sock'); descriptors = []
        def lock(fd, flags):
            descriptors.append(fd)
            if len(descriptors) == 2: raise BlockingIOError('fixture lock busy')
        self.flock.side_effect = lock
        with self.assertRaisesRegex(BlockingIOError, 'busy'): self.cleanup()
        self.assertEqual(len(list(self.root.iterdir())), 4); self.assertFalse(self.actions)
        self.assertFalse(self.proof['complete'])
        for fd in descriptors:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_unknown_entry_or_replaced_socket_is_preserved(self):
        self.capture(); peer = self.root / 'unknown'; peer.write_bytes(b'keep')
        with self.assertRaisesRegex(RuntimeError, 'Unknown'): self.cleanup()
        self.assertEqual(peer.read_bytes(), b'keep'); self.assertFalse(self.actions)
        with self.assertRaisesRegex(RuntimeError, 'one-use'): self.cleanup()
        peer.unlink(); self.files.attempted = False  # Separate synthetic attempt, never a native retry.
        old = self.root / 'left.sock'; old.rename(self.base / 'old-socket'); old.touch(mode=0o600)
        with self.assertRaisesRegex(RuntimeError, 'changed'): self.cleanup()
        self.assertTrue(old.exists()); self.assertFalse(self.actions)

    def test_missing_or_replaced_lock_refuses_other_cleanup(self):
        self.capture(); lock = self.root / 'left.sock.lock'; lock.rename(self.base / 'old-lock')
        with self.assertRaisesRegex(RuntimeError, 'missing or replaced'): self.cleanup()
        self.assertTrue((self.root / 'left.sock').exists()); self.assertFalse(self.actions)
        self.files.attempted = False; lock.touch(mode=0o600)
        with self.assertRaisesRegex(RuntimeError, 'changed'): self.cleanup()
        self.assertTrue(lock.exists()); self.assertFalse(self.actions)

    def test_parent_replacement_and_parent_alias_are_preserved(self):
        self.capture(); old = self.base / 'old-parent'; self.root.rename(old); self.root.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, 'identity changed'): self.cleanup()
        self.assertTrue((old / 'left.sock.lock').exists()); self.assertFalse(self.actions)
        self.root.rmdir(); self.root.symlink_to(old, target_is_directory=True); self.files.attempted = False
        with self.assertRaisesRegex(RuntimeError, 'node type'): self.cleanup()
        self.assertTrue(self.root.is_symlink())

    def test_capture_requires_live_app_and_exact_regular_private_single_link_lock(self):
        for suffix in ('', '.lock'): (self.root / ('left.sock' + suffix)).touch(mode=0o600)
        def failed(): raise RuntimeError('fixture app not live')
        with self.assertRaisesRegex(RuntimeError, 'not live'):
            self.files.capture('left.sock', failed, self.deadline)
        lock = self.root / 'left.sock.lock'; lock.write_bytes(b'x')
        with self.assertRaisesRegex(RuntimeError, 'size'): self.files.capture('left.sock', lambda: None, self.deadline)
        lock.write_bytes(b''); os.link(lock, self.base / 'alias')
        with self.assertRaisesRegex(RuntimeError, 'link count'): self.files.capture('left.sock', lambda: None, self.deadline)
        (self.base / 'alias').unlink(); lock.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'mode'): self.files.capture('left.sock', lambda: None, self.deadline)
        lock.unlink(); lock.symlink_to(self.base / 'missing')
        with self.assertRaisesRegex(RuntimeError, 'type'): self.files.capture('left.sock', lambda: None, self.deadline)
        self.assertEqual(self.files.captured, {}); self.flock.assert_not_called()

    def test_change_after_flock_and_incomplete_absence_block_all_removal(self):
        self.capture(); lock = self.root / 'left.sock.lock'
        def replaced(fd, flags):
            lock.rename(self.base / 'old'); lock.touch(mode=0o600)
        self.flock.side_effect = replaced
        with self.assertRaisesRegex(RuntimeError, 'changed'): self.cleanup()
        self.assertFalse(self.actions); self.assertTrue(lock.exists())
        self.files.attempted = False
        def gap(): raise RuntimeError('fixture incomplete process inventory')
        with self.assertRaisesRegex(RuntimeError, 'incomplete process'):
            self.files.cleanup(gap, self.deadline, self.actions, {})
        self.assertFalse(self.actions)

    def test_late_lock_acquisition_cannot_become_deletion_authority(self):
        self.capture(); now = [1.0]; self.deadline = 2.0
        self.flock.side_effect = lambda fd, flags: now.__setitem__(0, 2.0)
        with patch.object(probe.time, 'monotonic', side_effect=lambda: now[0]):
            with self.assertRaisesRegex(RuntimeError, 'deadline exhausted'): self.cleanup()
        self.assertFalse(self.actions); self.assertFalse(self.proof['complete'])
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_late_descriptor_release_cannot_report_complete(self):
        self.capture(); now = [1.0]; self.deadline = 2.0; original = os.close
        def late(fd):
            original(fd); now[0] = 2.0
        with patch.object(probe.time, 'monotonic', side_effect=lambda: now[0]), patch.object(probe.os, 'close', side_effect=late):
            with self.assertRaisesRegex(RuntimeError, 'deadline exhausted'): self.cleanup()
        self.assertFalse(self.proof['complete'])

    def test_final_live_check_failure_does_not_publish_a_capture(self):
        for suffix in ('', '.lock'): (self.root / ('left.sock' + suffix)).touch(mode=0o600)
        calls = []
        def live():
            calls.append(True)
            if len(calls) == 2: raise RuntimeError('fixture app changed before capture completed')
        with self.assertRaisesRegex(RuntimeError, 'app changed'): self.files.capture('left.sock', live, self.deadline)
        self.assertEqual(self.files.captured, {}); self.flock.assert_not_called()

    def test_partial_removal_deadline_is_failed_and_not_retried(self):
        self.capture(); now = [1.0]; self.deadline = 2.0; original = os.unlink
        def late(name, **kwargs):
            original(name, **kwargs); now[0] = 2.0
        with patch.object(probe.time, 'monotonic', side_effect=lambda: now[0]), patch.object(probe.os, 'unlink', side_effect=late):
            with self.assertRaisesRegex(RuntimeError, 'deadline exhausted'): self.cleanup()
        self.assertEqual(len(self.actions), 1); self.assertFalse(self.proof['complete'])
        self.assertTrue((self.root / 'left.sock.lock').exists())
        with self.assertRaisesRegex(RuntimeError, 'one-use'): self.cleanup()

    def test_peer_appearing_after_first_unlink_preserves_peer_and_remaining_lock(self):
        self.capture(); original = os.unlink
        def appeared(name, **kwargs):
            original(name, **kwargs); (self.root / 'unknown').write_bytes(b'keep')
        with patch.object(probe.os, 'unlink', side_effect=appeared):
            with self.assertRaisesRegex(RuntimeError, 'Unknown'): self.cleanup()
        self.assertEqual((self.root / 'unknown').read_bytes(), b'keep')
        self.assertTrue((self.root / 'left.sock.lock').exists()); self.assertFalse(self.proof['complete'])

    def test_reappeared_parent_after_rmdir_cannot_report_complete(self):
        self.capture(); original = os.rmdir
        def replaced(path, **kwargs):
            original(path, **kwargs); self.root.mkdir(mode=0o700); (self.root / 'peer').write_bytes(b'keep')
        with patch.object(probe.os, 'rmdir', side_effect=replaced):
            with self.assertRaisesRegex(RuntimeError, 'reappeared'): self.cleanup()
        self.assertEqual((self.root / 'peer').read_bytes(), b'keep'); self.assertFalse(self.proof['complete'])


class FakeLifecycleTests(RunFixture):
    def scenario(self, *, stage_gap=None):
        current, processes, apps, reloads, inputs = {}, {}, {}, [], []
        clock = [1000.0]
        self.stack.enter_context(patch.object(probe.time, 'monotonic', side_effect=lambda: clock[0]))
        self.stack.enter_context(patch.object(probe.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
        self.stack.enter_context(patch.object(probe, 'snapshot', side_effect=lambda *args, **kwargs: dict(current)))
        self.stack.enter_context(patch.object(probe, 'record', side_effect=lambda pid, *args, **kwargs: current.get(pid)))
        self.stack.enter_context(patch.object(probe.fcntl, 'flock'))
        original_entry, original_hash = probe.entry_at, probe.sha256
        def entry(fd, name):
            value = original_entry(fd, name)
            if value and name in ('left.sock', 'right.sock') and stat.S_ISREG(value['mode']):
                value['mode'] = stat.S_IFSOCK | 0o600
            return value
        self.stack.enter_context(patch.object(probe, 'entry_at', side_effect=entry))
        self.stack.enter_context(patch.object(probe, 'sha256', side_effect=lambda path: self.digest if str(path).startswith('/proc/') else original_hash(path)))
        self.stack.enter_context(patch.object(Path, 'is_socket', lambda path: path.exists() and
                                  (path == self.root / 'wayland' or path.name in ('left.sock', 'right.sock'))))
        def popen(argv, **kwargs):
            self.assertEqual(argv, [str(self.payload / 'bin/agterm-linux')])
            env = kwargs['env']; name = Path(env['AGTERM_CONTROL_SOCKET']).stem; pid = 4000 + len(apps)
            process = SimpleNamespace(pid=pid, returncode=None); process.poll = lambda: process.returncode
            processes[pid] = process; current[pid] = row(pid, tagged=True); apps[pid] = env
            owner = Path(env['XDG_RUNTIME_DIR']) / ('agterm-ghostty-' + name); owner.mkdir(mode=0o700)
            for theme in ('lightTheme.conf', 'darkTheme.conf'): (owner / theme).touch(mode=0o600)
            for suffix in ('', '.lock'): Path(env['AGTERM_CONTROL_SOCKET'] + suffix).touch(mode=0o600)
            peer = Path(env['XDG_RUNTIME_DIR']) / 'dconf'
            if not peer.exists():
                peer.mkdir(mode=0o700); (peer / 'user').write_bytes(b'\0\0'); (peer / 'user').chmod(0o600)
            else:
                old = (peer / 'user').stat(); os.utime(peer / 'user', ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
            return process
        self.stack.enter_context(patch.object(probe.subprocess, 'Popen', side_effect=popen))
        def command(instance, arguments, env, **kwargs):
            self.assertEqual(arguments, ['hyprctl', 'clients', '-j'])
            return json.dumps([dict(pid=pid, mapped=True, xwayland=False) for pid in apps])
        self.stack.enter_context(patch.object(probe.Probe, 'command', command))
        def ctl(instance, app, *args):
            if args == ('tree',): return dict(tree=dict(workspaces=[dict(sessions=[dict(id=app['name'], realized=True)])]))
            if args[:2] == ('config', 'reload'): reloads.append(app['name']); return {}
            if args[:2] == ('session', 'text'): return dict(text='AGTERM_CONFIG_NOISE_STARTED')
            self.assertEqual(args[:2], ('session', 'type')); self.assertTrue(args[2].startswith('python3 '))
            current[9000] = row(9000, parent=app['process'].pid, tagged=True)
            (app['state'] / 'noise-started.json').write_text(json.dumps(dict(pid=9000, titles_sent=100)))
            return {}
        self.stack.enter_context(patch.object(probe.Probe, 'ctl', ctl))
        self.stack.enter_context(patch.object(probe.Probe, 'input', lambda instance, app, label, code=None: inputs.append(label)))
        def send(owner, target, sig):
            self.assertEqual(sig, signal.SIGTERM); pid = target['pid']; self.assertIn(probe.identity(target), owner.rows)
            env = apps[pid]; processes[pid].returncode = 0; current.pop(pid)
            current.pop(9000, None)
            folder = Path(env['XDG_RUNTIME_DIR']) / ('agterm-ghostty-' + Path(env['AGTERM_CONTROL_SOCKET']).stem)
            for file in folder.iterdir(): file.unlink()
            folder.rmdir(); Path(env['AGTERM_CONTROL_SOCKET']).unlink()
            return True
        self.stack.enter_context(patch.object(probe.OwnedProcesses, 'send', send))
        original_observe = probe.Probe.observe_runtime
        def observe(instance, stage, deadline):
            if stage == stage_gap: (instance.runtime / 'unexpected-peer').touch()
            return original_observe(instance, stage, deadline)
        self.stack.enter_context(patch.object(probe.Probe, 'observe_runtime', observe))
        return reloads, inputs

    def test_full_real_probe_flow_uses_inventory_at_every_site_and_cleans_only_own_locks(self):
        reloads, inputs = self.scenario(); result = self.run_probe()
        self.assertTrue(result['passed'], result['errors'])
        self.assertEqual(len(result['checks']), 4); self.assertEqual(len(result['apps']), 2)
        self.assertEqual(len(reloads), 32)
        self.assertEqual(inputs, ['initial-left', 'initial-right', 'peer-survived', 'backgrounds'])
        self.assertEqual(result['noisy_titles_written_before_shutdown'], 100)
        self.assertEqual({r['stage'] for r in result['runtime_observations']},
                         {'first-app', 'two-apps', 'first-shutdown', 'reloads', 'final-shutdown', 'cleanup'})
        self.assertTrue(all(r['complete'] for r in result['runtime_observations']))
        self.assertTrue(result['runtime_observation_complete']); self.assertFalse(result['runtime_observation_gaps'])
        self.assertTrue(any(r['peer_refreshes'] for r in result['runtime_observations']))
        self.assertEqual(set(result['control_file_captures']), {'left.sock', 'right.sock'})
        for capture in result['control_file_captures'].values():
            self.assertEqual(stat.S_IMODE(capture['parent']['mode']), 0o700)
            self.assertEqual(capture['app_sha256'], self.digest)
            self.assertIn(capture['process'], [app['process'] for app in result['apps']])
        self.assertTrue(result['control_cleanup']['complete'])
        self.assertEqual(result['control_cleanup']['locks_acquired'], ['left.sock.lock', 'right.sock.lock'])
        self.assertGreater(result['control_cleanup']['absence_checks'], 0)
        self.assertEqual(result['process_absence_policy'], 'unchanged-prelaunch-exclusion/1')
        self.assertEqual(result['prelaunch_processes'], [])
        self.assertEqual(len(result['process_absence_observations']), result['control_cleanup']['absence_checks'])
        self.assertTrue(all(item['complete'] and item['captured_pids'] == item['absent_captured_pids']
                            for item in result['process_absence_observations']))
        self.assertEqual(result['cleanup_actions'], [])
        self.assertEqual([a['name'] for a in result['control_cleanup']['actions']], ['left.sock.lock', 'right.sock.lock'])
        self.assertEqual((self.root / 'output/shared-runtime/dconf/user').read_bytes(), b'\0\0')
        self.assertEqual(result['schema'], 'agterm-config-runtime-probe/2')
        self.assertEqual(result['probe_sha256'], probe.sha256(SOURCE))

    def test_final_shutdown_gap_keeps_original_inner_error_and_cannot_pass(self):
        self.scenario(stage_gap='final-shutdown'); result = self.run_probe()
        self.assertFalse(result['passed']); self.assertFalse(result['runtime_observation_complete'])
        self.assertIn('Unknown runtime peer', str(result['errors']))
        self.assertTrue((self.root / 'output/shared-runtime/unexpected-peer').exists())

    def test_cleanup_observation_gap_cannot_pass_after_all_exercise_checks(self):
        self.scenario(stage_gap='cleanup'); result = self.run_probe()
        self.assertEqual(len(result['checks']), 4); self.assertFalse(result['passed'])
        self.assertFalse(result['runtime_observation_complete']); self.assertTrue(result['runtime_observation_gaps'])

    def test_stable_observation_timeout_retains_concrete_peer_error(self):
        self.scenario(stage_gap='first-app'); result = self.run_probe()
        self.assertFalse(result['passed']); self.assertIn('inner errors=', str(result['errors']))
        self.assertIn('Unknown runtime peer', str(result['errors']))

    def test_two_app_observer_rejects_a_peer_gap(self):
        self.scenario(stage_gap='two-apps'); result = self.run_probe()
        self.assertFalse(result['passed']); self.assertIn('Unknown runtime peer', str(result['errors']))

    def test_first_shutdown_observer_rejects_a_peer_gap(self):
        self.scenario(stage_gap='first-shutdown'); result = self.run_probe()
        self.assertFalse(result['passed']); self.assertIn('Unknown runtime peer', str(result['errors']))

    def test_reload_observer_rejects_a_peer_gap(self):
        self.scenario(stage_gap='reloads'); result = self.run_probe()
        self.assertFalse(result['passed']); self.assertIn('Unknown runtime peer', str(result['errors']))

    def test_transient_unknown_peer_is_latched_after_it_disappears(self):
        output = self.root / 'out'; output.mkdir(mode=0o700)
        instance = probe.Probe(self.payload, output, self.env, self.digest, 2)
        peer = instance.runtime / 'unknown'; peer.touch()
        instance.observe_runtime('first-app', instance.deadline); peer.unlink()
        after = instance.observe_runtime('cleanup', instance.deadline)
        self.assertTrue(after['complete']); self.assertFalse(instance.report['runtime_observation_complete'])
        self.assertIn('Unknown runtime peer', str(instance.report['runtime_observation_gaps']))

    def test_absence_refuses_zombies_pid_reuse_and_incomplete_inventory(self):
        output = self.root / 'out'; output.mkdir(mode=0o700)
        instance = probe.Probe(self.payload, output, self.env, self.digest, 2)
        instance.owned.add(row(20))
        for observed in (row(20, state='Z'), row(20, start='reused'), row(20)):
            with self.subTest(observed=observed), patch.object(probe, 'record', return_value=observed):
                with self.assertRaisesRegex(RuntimeError, 'still exists or was reused'): instance.require_process_absence()
        with patch.object(probe, 'snapshot', side_effect=RuntimeError('fixture incomplete inventory')):
            with self.assertRaisesRegex(RuntimeError, 'incomplete inventory'): instance.require_process_absence()
        with patch.object(probe, 'record', return_value=None) as read:
            instance.require_process_absence(); read.assert_called_once_with(20, instance.token, strict=True)

    def test_incomplete_proc_identity_is_never_silently_absent(self):
        # Fake every /proc access; the test never consults the host process inventory.
        with patch.object(Path, 'read_text', side_effect=PermissionError('fixture proc unreadable')), \
                patch.object(probe, 'process_exists', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'Incomplete process identity'): probe.record(20, strict=True)
            self.assertIsNone(probe.record(20))
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError()), \
                patch.object(probe, 'process_exists', return_value=False):
            self.assertIsNone(probe.record(20, strict=True))


if __name__ == '__main__':
    unittest.main()
