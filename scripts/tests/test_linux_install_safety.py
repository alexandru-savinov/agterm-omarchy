#!/usr/bin/env python3
"""Exercise installer interruption and physical-directory launcher links in temporary fixtures."""
import os
from pathlib import Path
import shutil
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

    def test_sigterm_after_completed_replacement_keeps_new_install(self):
        self.existing_install()
        self.intercept_moves('signal-after-install')
        self.fixture.install('--no-build', '--default-rendering', success=False)
        self.assertTrue(Path(self.fixture.env['MOVE_EVENT']).exists())
        self.assertFalse((self.fixture.prefix / 'lib/agterm/keep').exists())
        self.assertEqual((self.fixture.prefix / 'lib/agterm/share/agterm/RENDERER').read_text(), 'default\n')
        self.assertTrue((self.fixture.prefix / 'bin/agterm-linux').exists())
        self.assertEqual(list((self.fixture.prefix / 'lib').glob('.agterm-install.*')), [])

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
