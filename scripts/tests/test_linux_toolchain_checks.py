#!/usr/bin/env python3
"""Exercise the Swift toolchain selection and the GTK pkg-config check with fake tools on PATH."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which('bash')
MODULES = ('gtk4', 'libadwaita-1', 'webkitgtk-6.0', 'epoxy')


class ToolchainFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.bin = self.work / 'bin'
        self.bin.mkdir()

    def tool(self, name, body):
        path = self.bin / name
        path.write_text(f'#!/bin/sh\n{body}\n')
        path.chmod(0o755)

    def env(self, **extra):
        return {'PATH': str(self.bin), 'HOME': str(self.work / 'home'),
                'XDG_DATA_HOME': str(self.work / 'data'), **extra}


class SwiftEnvTests(ToolchainFixture):
    def select(self, **extra):
        script = f'set -euo pipefail; source {ROOT / "linux/swift-env.sh"}; printf "%s\\n" "${{SWIFT_COMMAND[*]}}"'
        return subprocess.run([BASH, '-c', script], env=self.env(**extra), text=True, capture_output=True)

    def swift(self, version):
        self.tool('swift', f'echo "Swift version {version} (swift-{version}-RELEASE)"')

    def test_path_swift_is_used_only_at_the_pinned_version(self):
        self.tool('mise', 'exit 0')
        for version, expected in (('6.3.2', 'swift'), ('6.2.1', 'mise exec swift@6.3.2 -- swift'),
                                  ('6.3.20', 'mise exec swift@6.3.2 -- swift')):
            with self.subTest(version=version):
                self.swift(version)
                result = self.select()
                self.assertEqual((result.returncode, result.stdout.strip()), (0, expected), result.stderr)

    def test_a_wrong_path_swift_without_mise_fails_and_names_both_versions(self):
        self.swift('6.2.1')
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PATH swift is 6.2.1, not 6.3.2', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_an_unreadable_version_is_not_the_pin(self):
        self.tool('swift', 'exit 3')
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PATH swift is an unknown version', result.stderr)

    def test_explicit_swift_override_wins_over_any_version(self):
        self.swift('6.2.1')
        result = self.select(SWIFT='/opt/swift/bin/swift')
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '/opt/swift/bin/swift'))

    def test_no_swift_and_no_mise_still_asks_for_setup(self):
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Swift is required', result.stderr)


class SetupOmarchyPkgConfigTests(ToolchainFixture):
    def run_setup(self, missing):
        script = (ROOT / 'scripts/setup-omarchy.sh').read_text()
        self.assertEqual(script.count('source /etc/os-release'), 1)
        os_release = self.work / 'os-release'
        os_release.write_text('ID=arch\n')
        checkout = self.work / 'checkout'
        (checkout / 'scripts').mkdir(parents=True)
        (checkout / 'linux').mkdir()
        (checkout / 'linux/arch.sh').write_text('')
        setup = checkout / 'scripts/setup-omarchy.sh'
        setup.write_text(script.replace('source /etc/os-release', f'source {os_release}'))
        calls = self.work / 'pkg-config.calls'
        self.tool('pkg-config', f'echo "$*" >> {calls}\ncase " $MISSING " in *" $2 "*) exit 1 ;; esac')
        self.tool('mise', 'exit 97')
        for name in ('uname', 'dirname'):
            os.symlink(shutil.which(name), self.bin / name)
        result = subprocess.run([BASH, str(setup), '--skip-packages'], env=self.env(MISSING=' '.join(missing)),
                                text=True, capture_output=True)
        return result, calls.read_text().splitlines()

    @unittest.skipIf(os.geteuid() == 0, 'setup-omarchy.sh refuses to run as root')
    def test_each_missing_module_is_named_before_exit(self):
        result, calls = self.run_setup(['libadwaita-1', 'epoxy'])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('missing pkg-config modules: libadwaita-1 epoxy;', result.stderr)
        self.assertEqual(calls, [f'--exists {module}' for module in MODULES])

    @unittest.skipIf(os.geteuid() == 0, 'setup-omarchy.sh refuses to run as root')
    def test_present_modules_pass_the_check(self):
        result, calls = self.run_setup([])
        self.assertNotIn('missing pkg-config modules', result.stderr)
        self.assertEqual(calls, [f'--exists {module}' for module in MODULES])


if __name__ == '__main__':
    unittest.main()
