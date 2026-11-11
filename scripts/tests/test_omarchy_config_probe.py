"""Disposable safety fixtures for the real probe helpers; no frontend, display or VM execution."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
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

    def test_dconf_is_not_exempted_from_unknown_runtime_entries(self):
        foreign = self.root / 'dconf'; foreign.mkdir(mode=0o700)
        marker = foreign / 'user'; marker.write_bytes(b'preserve foreign runtime data')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected shared-runtime entry'):
            probe.configs(self.root, 2)
        self.assertEqual(marker.read_bytes(), b'preserve foreign runtime data')


class RunSafetyTests(unittest.TestCase):
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
        self.wayland = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.wayland.close)
        self.wayland.bind(str(self.root / 'wayland'))
        self.env = dict(PATH='/usr/bin:/bin', WAYLAND_DISPLAY='wayland', XDG_RUNTIME_DIR=str(self.root),
                        DBUS_SESSION_BUS_ADDRESS='unix:path=/fixture/no-bus')
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(probe.os, 'uname', return_value=SimpleNamespace(sysname='Linux')))
        self.stack.enter_context(patch.object(probe.os, 'pidfd_open', create=True))
        self.stack.enter_context(patch.object(probe.signal, 'pidfd_send_signal', create=True))
        self.stack.enter_context(patch.object(probe, 'snapshot', return_value={}))
        self.short = tempfile.TemporaryDirectory(prefix='agt-cfg-test-', dir='/tmp')
        self.addCleanup(self.short.cleanup)
        self.short_output = tempfile.TemporaryDirectory(prefix='agt-out-', dir='/tmp')
        self.addCleanup(self.short_output.cleanup)
        self.stack.enter_context(patch.object(probe.tempfile, 'mkdtemp', return_value=self.short.name))

    def run_probe(self, **options):
        return probe.run_probe(self.payload, self.root / 'output', self.env, self.digest, **options)

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
        with patch.object(probe, 'snapshot', side_effect=lambda token=None: dict(current)), \
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
            self.assertNotIn('AGTERM_CONTROL_SOCKET', env)
            self.assertTrue(env['AGTERM_STATE_DIR'].startswith('/tmp/agt-cfg-'))
            self.assertLess(len((env['AGTERM_STATE_DIR'] + '/agterm.sock').encode()), 100)
            self.assertEqual(Path(env['AGTERM_STATE_DIR']).resolve(), self.root / 'output/left-state')
            self.assertEqual(env['GSETTINGS_BACKEND'], 'memory')
            raise OSError('stop before any frontend')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(result['errors'], ['OSError: stop before any frontend'])
        self.assertEqual(self.env, original)

    def bound_socket_fixture(self, exercise):
        """Bind as Linux ControlResolve does; stub only the frontend/process/JSON endpoints."""
        servers, launched = {}, []
        original_hash = probe.sha256
        def launch(argv, **kwargs):
            env = kwargs['env']; pid = 710 + len(launched)
            # Deliberately ignore AGTERM_CONTROL_SOCKET, exactly like the Linux frontend.
            path = str(Path(env['AGTERM_STATE_DIR']) / 'agterm.sock')
            server = socket.socket(socket.AF_UNIX); self.addCleanup(server.close)
            server.bind(path); server.listen(1); servers[path] = server
            if env.get('GSETTINGS_BACKEND') != 'memory':
                (Path(env['XDG_RUNTIME_DIR']) / 'dconf').mkdir(exist_ok=True)
            cache = Path(env['XDG_CACHE_HOME']); cache.mkdir(parents=True)
            (cache / 'fixture-cache').write_text(str(pid))
            launched.append(dict(pid=pid, env=env))
            return SimpleNamespace(pid=pid, poll=lambda: None)
        def command(instance, arguments, env, **kwargs):
            if arguments[0] == 'hyprctl':
                self.assertEqual(env, self.env)
                return json.dumps([dict(pid=app['pid'], mapped=True, xwayland=False) for app in launched])
            path = arguments[arguments.index('--socket') + 1]
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(path)
                accepted, _ = servers[path].accept(); accepted.close()
            return json.dumps(dict(ok=True, result=dict(tree=dict(workspaces=[
                dict(sessions=[dict(id='fixture-session', realized=True)])]))))
        with patch.object(probe.subprocess, 'Popen', side_effect=launch), \
                patch.object(probe, 'record', side_effect=lambda pid: row(pid)), \
                patch.object(probe, 'sha256', side_effect=lambda path: self.digest if str(path).startswith('/proc/')
                             else original_hash(path)), patch.object(probe.Probe, 'command', command), \
                patch.object(probe.Probe, 'input'), \
                patch.object(probe.Probe, 'exercise', lambda instance: exercise(instance, launched)):
            return self.run_probe()

    def test_linux_socket_is_discovered_without_a_control_socket_override(self):
        self.root = Path(self.short_output.name).resolve()
        with patch.object(probe, 'LIMITS', dict(probe.LIMITS, ready=0.2)):
            result = self.bound_socket_fixture(lambda instance, launched: instance.launch('left'))
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['apps']), 1)
        self.assertTrue(result['socket_directory_removed'])

    def test_linux_state_socket_contract_with_long_output_and_isolated_gtk_children(self):
        self.root = self.root / ('retained-evidence-' + 'x' * 90); self.root.mkdir(mode=0o700)
        self.env.update(GSETTINGS_BACKEND='dconf', AGTERM_CONTROL_SOCKET='/unrelated/ignored.sock',
                        XDG_CACHE_HOME='/unrelated/cache')
        original = dict(self.env)
        def exercise(instance, launched):
            for name in ('left', 'right'):
                app = instance.launch(name)
                state = self.root / 'output' / (name + '-state')
                self.assertGreater(len(str(state / 'agterm.sock').encode()), 104)
                self.assertEqual(Path(app['env']['AGTERM_STATE_DIR']).resolve(), state)
                self.assertTrue(app['socket'].is_socket())
                self.assertLess(len(str(app['socket']).encode()), 100)
                self.assertEqual(app['env']['GSETTINGS_BACKEND'], 'memory')
                self.assertNotIn('AGTERM_CONTROL_SOCKET', app['env'])
                self.assertEqual(app['env']['XDG_CACHE_HOME'], str(self.root / 'output' / (name + '-home/.cache')))
            self.assertNotEqual(launched[0]['env']['AGTERM_STATE_DIR'], launched[1]['env']['AGTERM_STATE_DIR'])
            self.assertNotEqual(launched[0]['env']['XDG_CACHE_HOME'], launched[1]['env']['XDG_CACHE_HOME'])
            self.assertEqual(probe.configs(instance.runtime, 2), {})
        result = self.bound_socket_fixture(exercise)
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['apps']), 2)
        self.assertTrue(result['socket_directory_removed'])
        self.assertTrue(result['sentinel_preserved'])
        self.assertEqual(result['remaining_generated_paths'], [])
        for name in ('left', 'right'):
            state = self.root / 'output' / (name + '-state')
            self.assertFalse((state / 'agterm.sock').exists())
            self.assertTrue((state / 'config/ghostty.conf').is_file())
            self.assertTrue((self.root / 'output' / (name + '-home/.cache/fixture-cache')).is_file())
        self.assertEqual(self.env, original)

    def test_changed_state_alias_is_preserved_without_following_it(self):
        foreign = self.root / 'foreign'; foreign.mkdir(mode=0o700)
        marker = foreign / 'agterm.sock'; marker.write_bytes(b'keep unrelated file')
        aliases = []
        def launch(argv, **kwargs):
            link = Path(kwargs['env']['AGTERM_STATE_DIR']); aliases.append(link)
            link.rename(link.with_name('original-alias'))
            link.symlink_to(foreign, target_is_directory=True)
            raise OSError('fixture changed alias')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertIn('State alias identity or target changed', '\n'.join(result['errors']))
        self.assertEqual(aliases[0].resolve(), foreign)
        self.assertEqual(marker.read_bytes(), b'keep unrelated file')

    def test_same_target_alias_replacement_is_rejected_by_identity(self):
        def launch(argv, **kwargs):
            link = Path(kwargs['env']['AGTERM_STATE_DIR']); target = os.readlink(link)
            link.rename(link.with_name('original-alias')); link.symlink_to(target, target_is_directory=True)
            raise OSError('fixture replaced alias inode')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertIn('State alias identity or target changed', '\n'.join(result['errors']))
        self.assertTrue((Path(self.short.name) / 'left').is_symlink())

    def test_changed_state_directory_is_preserved(self):
        def launch(argv, **kwargs):
            state = Path(kwargs['env']['AGTERM_STATE_DIR']).resolve()
            state.rename(state.with_name('original-state')); state.mkdir(mode=0o700)
            (state / 'agterm.sock').write_bytes(b'keep replacement state')
            raise OSError('fixture replaced state inode')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertIn('State directory identity changed', '\n'.join(result['errors']))
        self.assertEqual((self.root / 'output/left-state/agterm.sock').read_bytes(), b'keep replacement state')

    def test_broken_socket_symlink_is_rejected_and_preserved(self):
        def launch(argv, **kwargs):
            (Path(kwargs['env']['AGTERM_STATE_DIR']) / 'agterm.sock').symlink_to(self.root / 'absent')
            raise OSError('fixture replaced socket')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertIn('Unexpected socket replacement', '\n'.join(result['errors']))
        self.assertTrue((self.root / 'output/left-state/agterm.sock').is_symlink())

    def test_socket_file_replacement_is_rejected_and_preserved(self):
        def launch(argv, **kwargs):
            (Path(kwargs['env']['AGTERM_STATE_DIR']) / 'agterm.sock').write_bytes(b'keep replacement file')
            raise OSError('fixture replaced socket')
        with patch.object(probe.subprocess, 'Popen', side_effect=launch):
            result = self.run_probe()
        self.assertIn('Unexpected socket replacement', '\n'.join(result['errors']))
        self.assertEqual((self.root / 'output/left-state/agterm.sock').read_bytes(), b'keep replacement file')

    def test_unexpected_dconf_remains_reported_and_preserved(self):
        def exercise(instance):
            (instance.runtime / 'dconf').mkdir(mode=0o700)
        with patch.object(probe.Probe, 'exercise', exercise):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertEqual(result['remaining_generated_paths'], ['dconf'])
        self.assertTrue((self.root / 'output/shared-runtime/dconf').is_dir())
        self.assertTrue(result['sentinel_preserved'])

    def test_partial_success_cannot_pass_without_both_apps_and_all_checks(self):
        with patch.object(probe.Probe, 'exercise', return_value=None):
            result = self.run_probe()
        self.assertFalse(result['passed'])
        self.assertTrue(result['sentinel_preserved'])


if __name__ == '__main__':
    unittest.main()
