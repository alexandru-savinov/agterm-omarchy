#!/usr/bin/env python3
"""Exercise installer interruption and physical-directory launcher links in temporary fixtures."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_linux_install as fixtures


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PersonalInstallTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def existing_install(self):
        fixture = self.fixture
        fixture.install('--no-build', '--software-rendering')
        (fixture.prefix / 'lib/agterm/keep').write_text('previous installation')

    def intercept_moves(self, behavior):
        fixture = self.fixture
        commands = fixture.work / 'commands'
        commands.mkdir()
        shim = commands / 'mv'
        shim.write_text(f'#!{sys.executable}\n' + '''import os, pathlib, signal, subprocess, sys
source, destination = map(pathlib.Path, sys.argv[1:])
behavior = os.environ['MOVE_BEHAVIOR']
if source.name == 'payload' and behavior in ('fail-install', 'fail-restore'):
    sys.exit(74)
if source.name == 'previous' and behavior == 'fail-restore':
    sys.exit(73)
result = subprocess.run([os.environ['REAL_MV'], *sys.argv[1:]], check=False)
if result.returncode:
    sys.exit(result.returncode)
if ((destination.name == 'previous' and behavior == 'signal-after-backup') or
        (source.name == 'payload' and behavior == 'signal-after-install')):
    pathlib.Path(os.environ['MOVE_EVENT']).write_text(str(destination))
    os.kill(os.getppid(), signal.SIGTERM)
''')
        shim.chmod(0o755)
        fixture.env.update(PATH=str(commands) + ':' + fixture.env['PATH'],
                           REAL_MV=shutil.which('mv'), MOVE_BEHAVIOR=behavior,
                           MOVE_EVENT=str(fixture.work / 'move-event'))

    def assert_previous_restored(self):
        fixture = self.fixture
        self.assertTrue((fixture.prefix / 'lib/agterm/keep').is_file(), 'previous payload was lost')
        self.assertEqual((fixture.prefix / 'lib/agterm/keep').read_text(), 'previous installation')
        self.assertEqual((fixture.prefix / 'lib/agterm/share/agterm/RENDERER').read_text(), 'software\n')
        for name in ('agterm-linux', 'agtermctl'):
            self.assertTrue((fixture.prefix / 'bin' / name).exists())
        self.assertEqual(list((fixture.prefix / 'lib').glob('.agterm-install.*')), [])

    def test_sigterm_after_backup_rename_restores_previous_install(self):
        self.existing_install()
        self.intercept_moves('signal-after-backup')
        result = self.fixture.install('--no-build', '--default-rendering', success=False)
        self.assertEqual(result.returncode, 143)
        self.assertEqual(Path(self.fixture.env['MOVE_EVENT']).read_text().split('/')[-1], 'previous')
        self.assert_previous_restored()

    def test_failed_payload_rename_restores_previous_install(self):
        self.existing_install()
        self.intercept_moves('fail-install')
        self.fixture.install('--no-build', '--default-rendering', success=False)
        self.assert_previous_restored()

    def test_failed_rollback_retains_backup_for_recovery(self):
        self.existing_install()
        self.intercept_moves('fail-restore')
        result = self.fixture.install('--no-build', '--default-rendering', success=False)
        backups = list((self.fixture.prefix / 'lib').glob('.agterm-install.*/previous'))
        self.assertEqual(len(backups), 1, 'cleanup must not delete the only remaining installation')
        self.assertEqual((backups[0] / 'keep').read_text(), 'previous installation')
        self.assertEqual((backups[0] / 'share/agterm/RENDERER').read_text(), 'software\n')
        self.assertIn(str(backups[0]), result.stderr)

    def test_sigterm_after_payload_replacement_restores_previous_install(self):
        self.existing_install()
        self.intercept_moves('signal-after-install')
        self.fixture.install('--no-build', '--default-rendering', success=False)
        self.assertTrue(Path(self.fixture.env['MOVE_EVENT']).exists())
        self.assert_previous_restored()

    def test_interrupted_upgrade_restores_payload_symlink(self):
        self.existing_install()
        payload = self.fixture.prefix / 'lib/agterm'
        external = self.fixture.work / 'external payload'
        payload.rename(external)
        payload.symlink_to(external, target_is_directory=True)
        self.intercept_moves('signal-after-backup')
        self.fixture.install('--no-build', success=False)
        self.assertTrue(payload.is_symlink())
        self.assertEqual(payload.resolve(), external)
        self.assert_previous_restored()

    def test_incomplete_payload_preserves_previous_install(self):
        self.existing_install()
        fixture = self.fixture
        archive = fixture.work / 'archive'
        subprocess.run(['bash', str(fixture.stage), str(archive)], check=True)
        for name in ('bin/agtermctl', 'bin/agterm-linux.bin', 'bin/agtermctl.bin', 'bin/zmx', 'share/pixmaps'):
            with self.subTest(missing=name):
                target = archive / name
                saved = fixture.work / 'saved'
                target.rename(saved)
                try:
                    result = fixture.install('--payload', str(archive), success=False)
                    self.assertIn('incomplete payload', result.stderr)
                    self.assert_previous_restored()
                finally:
                    saved.rename(target)

    def file_state(self):
        return {str(path.relative_to(self.fixture.prefix)):
                ('link', os.readlink(path)) if path.is_symlink() else (path.stat().st_mode, path.read_bytes())
                for path in self.fixture.prefix.rglob('*') if path.is_symlink() or path.is_file()}

    def fail_final_copy(self, signal_after=False):
        fixture = self.fixture
        commands = fixture.work / 'copy-commands'
        commands.mkdir()
        shim = commands / 'cp'
        shim.write_text(f'#!{sys.executable}\n' + '''import os, signal, subprocess, sys
result = subprocess.run([os.environ['REAL_CP'], *sys.argv[1:]], check=False)
if sys.argv[1] == '-R' and '/share/pixmaps/' in sys.argv[2]:
    if os.environ.get('SIGNAL_AFTER_COPY') == '1':
        os.kill(os.getppid(), signal.SIGTERM)
    sys.exit(74)
sys.exit(result.returncode)
''')
        shim.chmod(0o755)
        fixture.env.update(PATH=str(commands) + ':' + fixture.env['PATH'], REAL_CP=shutil.which('cp'),
                           SIGNAL_AFTER_COPY='1' if signal_after else '0')

    def assert_finalization_rollback(self, signal_after=False):
        fixture = self.fixture
        fixture.env['OLD_ICON'] = '1'
        self.existing_install()
        del fixture.env['OLD_ICON']
        unrelated = fixture.prefix / 'share/pixmaps/unrelated.png'
        unrelated.write_text('user file')
        config = fixture.work / 'home/.config/agterm/settings.json'
        config.parent.mkdir(parents=True)
        config.write_text('user settings')
        state = fixture.work / 'data/agterm/session.json'
        state.parent.mkdir(parents=True)
        state.write_text('user session')
        before = self.file_state()
        with fixture.stage.open('a') as output:
            output.write('printf new > "$1/share/pixmaps/new.png"\n')
        self.fail_final_copy(signal_after)
        fixture.install('--no-build', '--default-rendering', success=False)
        self.assert_previous_restored()
        self.assertEqual(self.file_state(), before)
        self.assertEqual(config.read_text(), 'user settings')
        self.assertEqual(state.read_text(), 'user session')
        for name in ('agterm-linux', 'agtermctl'):
            result = subprocess.run([str(fixture.prefix / 'bin' / name)], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (0, 'fixture'))

    def test_post_replacement_copy_failure_restores_files_and_launchers(self):
        self.assert_finalization_rollback()

    def test_signal_after_external_files_changed_restores_previous_install(self):
        self.assert_finalization_rollback(signal_after=True)

    def test_failed_first_install_removes_new_managed_files(self):
        self.fail_final_copy()
        self.fixture.install('--no-build', success=False)
        self.assertEqual(self.file_state(), {})
        self.assertFalse((self.fixture.prefix / 'lib/agterm').exists())

    def test_post_replacement_failure_restores_linked_payload_and_bin(self):
        fixture = self.fixture
        actual_bin = fixture.work / 'actual-bin'
        actual_bin.mkdir()
        fixture.prefix.mkdir()
        (fixture.prefix / 'bin').symlink_to(actual_bin, target_is_directory=True)
        self.existing_install()
        payload = fixture.prefix / 'lib/agterm'
        original = fixture.work / 'original-payload'
        payload.rename(original)
        payload.symlink_to(original, target_is_directory=True)
        self.fail_final_copy()
        fixture.install('--no-build', success=False)
        self.assert_previous_restored()
        self.assertEqual(payload.resolve(), original)
        self.assertTrue(payload.is_symlink())
        self.assertEqual((fixture.prefix / 'bin').resolve(), actual_bin)

    def test_failed_external_rollback_retains_recovery_files(self):
        self.existing_install()
        self.fail_final_copy()
        fixture = self.fixture
        shim = fixture.work / 'copy-commands/python3'
        shim.write_text(f'#!{sys.executable}\n' + '''import os, sys
if len(sys.argv) > 3 and sys.argv[3] == 'restore':
    sys.exit(75)
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
        shim.chmod(0o755)
        result = fixture.install('--no-build', success=False)
        backups = list((fixture.prefix / 'lib').glob('.agterm-install.*/external.json'))
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0].parent), result.stderr)
        self.assertTrue((backups[0].parent / 'external/bin/agtermctl').is_symlink())
        self.assertEqual((fixture.prefix / 'lib/agterm/keep').read_text(), 'previous installation')

    def test_payload_markers_do_not_follow_external_symlinks(self):
        fixture = self.fixture
        archive = fixture.work / 'archive'
        subprocess.run(['bash', str(fixture.stage), str(archive)], check=True)
        unrelated = fixture.work / 'user-config'
        unrelated.write_text('preserve')
        for name in ('RENDERER', 'INSTALLED_FILES'):
            (archive / 'share/agterm' / name).symlink_to(unrelated)
        fixture.install('--payload', str(archive))
        self.assertEqual(unrelated.read_text(), 'preserve')

    def test_payload_cannot_redirect_mutable_directory_outside_stage(self):
        self.existing_install()
        fixture = self.fixture
        archive = fixture.work / 'archive'
        subprocess.run(['bash', str(fixture.stage), str(archive)], check=True)
        shared = archive / 'share/agterm'
        external = fixture.work / 'external-config'
        shared.rename(external)
        (external / 'RENDERER').write_text('unchanged')
        shared.symlink_to(external, target_is_directory=True)
        fixture.install('--payload', str(archive), success=False)
        self.assertEqual((external / 'RENDERER').read_text(), 'unchanged')
        self.assert_previous_restored()

    def test_successful_upgrade_commits_new_payload(self):
        self.existing_install()
        self.fixture.install('--no-build', '--default-rendering')
        self.assertFalse((self.fixture.prefix / 'lib/agterm/keep').exists())
        for name in ('agterm-linux', 'agtermctl'):
            result = subprocess.run([str(self.fixture.prefix / 'bin' / name)], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (0, 'fixture'))
        self.assertEqual(list((self.fixture.prefix / 'lib').glob('.agterm-install.*')), [])

    def assert_launchers(self, physical_prefix):
        for name in ('agterm-linux', 'agtermctl'):
            link = self.fixture.prefix / 'bin' / name
            self.assertTrue(link.exists(), str(link))
            self.assertFalse(Path(os.readlink(link)).is_absolute())
            self.assertEqual(link.resolve(), physical_prefix / 'lib/agterm/bin' / name)

    def test_linked_bin_resolves_both_launchers_on_install_and_reinstall(self):
        fixture = self.fixture
        actual_bin = fixture.work / 'actual bin'
        actual_bin.mkdir()
        fixture.prefix.mkdir()
        (fixture.prefix / 'bin').symlink_to(actual_bin, target_is_directory=True)
        fixture.install('--no-build', '--software-rendering')
        self.assert_launchers(fixture.prefix)
        fixture.install('--no-build')
        self.assert_launchers(fixture.prefix)
        self.assertEqual((fixture.prefix / 'lib/agterm/share/agterm/RENDERER').read_text(), 'software\n')
        desktop = (fixture.prefix / 'share/applications/io.github.melonamin.agterm.desktop').read_text()
        self.assertIn(f'Exec="{fixture.prefix}/bin/agterm-linux"', desktop)

    def test_linked_prefix_and_bin_resolve_launchers(self):
        fixture = self.fixture
        physical_prefix = fixture.work / 'physical prefix'
        physical_prefix.mkdir()
        fixture.prefix.symlink_to(physical_prefix, target_is_directory=True)
        actual_bin = fixture.work / 'actual bin'
        actual_bin.mkdir()
        (physical_prefix / 'bin').symlink_to(actual_bin, target_is_directory=True)
        fixture.install('--no-build')
        self.assert_launchers(physical_prefix)

    def test_ordinary_prefix_remains_relocatable(self):
        fixture = self.fixture
        fixture.install('--no-build')
        moved = fixture.work / 'moved prefix'
        fixture.prefix.rename(moved)
        fixture.prefix = moved
        self.assert_launchers(moved)


if __name__ == '__main__':
    unittest.main()
