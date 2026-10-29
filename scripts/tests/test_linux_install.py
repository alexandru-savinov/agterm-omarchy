#!/usr/bin/env python3
"""Exercise the installer with a staged payload, without Swift or a graphical session."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PersonalInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / 'checkout'
        for name in ('scripts/install-linux.sh', 'scripts/run-linux.sh', 'linux/swift-env.sh'):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.repo / 'agterm-linux').mkdir()
        self.prefix = self.work / 'prefix with spaces'
        self.env = dict(os.environ, HOME=str(self.work / 'home'), XDG_DATA_HOME=str(self.work / 'data'))
        self.env.pop('PREFIX', None)
        self.env.pop('SWIFT', None)
        self.stage = self.repo / 'scripts/stage-linux.sh'
        self.stage.write_text('''#!/usr/bin/env bash
set -eu
[[ "${FAIL_STAGE:-0}" != 1 ]] || exit 42
mkdir -p "$1/bin" "$1/lib" "$1/share/agterm/agent-skill" "$1/share/agterm/agent-status" \
  "$1/share/ghostty/shell-integration" "$1/share/terminfo/x" "$1/share/applications" \
  "$1/share/pixmaps" "$1/share/icons/hicolor/scalable/actions"
for name in agterm-linux agtermctl zmx; do printf 'fixture\\n' > "$1/bin/$name"; done
printf library > "$1/lib/libswiftCore.so"
printf theme > "$1/share/ghostty/theme"
printf entry > "$1/share/terminfo/x/xterm-ghostty"
printf icon > "$1/share/pixmaps/io.github.melonamin.agterm.png"
printf hook > "$1/share/agterm/agent-status/hook.sh"
cat > "$1/share/applications/io.github.melonamin.agterm.desktop" <<'DESKTOP'
[Desktop Entry]
Exec=agterm-linux
[Desktop Action QuickTerminal]
Exec=agterm-linux --desktop-action=quick-terminal
DESKTOP
''')
        self.stage.chmod(0o755)

    def install(self, *args, success=True):
        result = subprocess.run(['bash', str(self.repo / 'scripts/install-linux.sh'),
                                 '--prefix', str(self.prefix), *args],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_complete_payload_survives_checkout_removal(self):
        self.install('--no-build')
        shutil.rmtree(self.repo)
        payload = self.prefix / 'lib/agterm'
        for name in ('bin/zmx', 'lib/libswiftCore.so', 'share/agterm/agent-status/hook.sh',
                     'share/terminfo/x/xterm-ghostty', 'share/ghostty/theme'):
            self.assertTrue((payload / name).is_file(), name)
        self.assertEqual((self.prefix / 'bin/agtermctl').resolve(), payload / 'bin/agtermctl')
        self.assertTrue((self.prefix / 'share/pixmaps/io.github.melonamin.agterm.png').exists())
        desktop = (self.prefix / 'share/applications/io.github.melonamin.agterm.desktop').read_text()
        self.assertIn(f'Exec="{self.prefix}/bin/agterm-linux"\n', desktop)
        self.assertIn(f'Exec="{self.prefix}/bin/agterm-linux" --desktop-action=quick-terminal', desktop)

    def test_staging_failure_preserves_existing_install(self):
        self.install('--no-build')
        marker = self.prefix / 'lib/agterm/keep'
        marker.write_text('old installation')
        self.env['FAIL_STAGE'] = '1'
        self.install('--no-build', success=False)
        self.assertEqual(marker.read_text(), 'old installation')
        self.assertTrue((self.prefix / 'bin/agtermctl').exists())
        self.assertEqual(list((self.prefix / 'lib').glob('.agterm-install.*')), [])

    def test_reinstall_replaces_payload_without_touching_unrelated_files(self):
        self.install('--no-build')
        (self.prefix / 'lib/agterm/old').touch()
        unrelated = self.prefix / 'share/pixmaps/unrelated.png'
        unrelated.write_text('preserve')
        self.install('--no-build')
        self.assertFalse((self.prefix / 'lib/agterm/old').exists())
        self.assertEqual(unrelated.read_text(), 'preserve')

    def test_explicit_swift_and_compat_environment(self):
        swift = self.work / 'chosen-swift'
        swift.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$HOME/swift-args"\nprintf "%s" "$LD_LIBRARY_PATH" > "$HOME/swift-libs"\n')
        swift.chmod(0o755)
        Path(self.env['HOME']).mkdir()
        compat = Path(self.env['XDG_DATA_HOME']) / 'agterm/swift-compat'
        compat.mkdir(parents=True)
        self.env.update(SWIFT=str(swift), LD_LIBRARY_PATH='/existing/library')
        self.install()
        self.assertEqual((Path(self.env['HOME']) / 'swift-args').read_text(), 'build\n-c\nrelease\n')
        self.assertEqual((Path(self.env['HOME']) / 'swift-libs').read_text(), f'{compat}:/existing/library')
        self.assertEqual(list(compat.iterdir()), [])

    def test_run_selects_requested_build_configuration(self):
        output = self.work / 'release product'
        output.mkdir()
        executable = output / 'AgtermLinux'
        executable.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$AGTERM_GHOSTTY_RESOURCES" "$AGTERM_ZMX_PATH" > "$HOME/launched"\n')
        executable.chmod(0o755)
        swift = self.work / 'chosen-swift'
        swift.write_text('#!/usr/bin/env bash\n[[ "$*" == "build -c release"* ]] || exit 11\nif [[ "$*" == *--show-bin-path ]]; then printf "%s\\n" "$FAKE_BIN"; fi\n')
        swift.chmod(0o755)
        Path(self.env['HOME']).mkdir()
        self.env.update(SWIFT=str(swift), FAKE_BIN=str(output))
        result = subprocess.run(['bash', str(self.repo / 'scripts/run-linux.sh'), '-c', 'release'],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((Path(self.env['HOME']) / 'launched').read_text().splitlines(), [
            str(self.repo / 'agterm-linux/vendor/ghostty/share/ghostty'),
            str(self.repo / 'agterm-linux/vendor/zmx/zmx')])

    def test_invalid_option_does_not_create_install(self):
        self.install('--bogus', success=False)
        self.assertFalse(self.prefix.exists())


class PayloadStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/stage-linux.sh', self.repo / 'scripts/stage-linux.sh')
        for name in ('verify-linux-resources.sh', 'verify-linux-zmx-cache.sh'):
            script = self.repo / 'scripts' / name
            script.write_text('#!/bin/sh\nexit 0\n')
            script.chmod(0o755)
        for directory in ('agterm-linux/.build/release/agterm-linux_AgtermLinux.resources/hud',
                          'agterm-linux/vendor/ghostty/share/ghostty',
                          'agterm-linux/vendor/ghostty/share/terminfo',
                          'agterm-linux/vendor/zmx', 'agterm/Resources/agent-status',
                          'plugins/agterm/skills/agterm', 'agterm/AppIcon.icon/Assets',
                          'packaging/linux'):
            (self.repo / directory).mkdir(parents=True)
        for name in ('AgtermLinux', 'agtermctl-linux', 'agterm-linux_AgtermLinux.resources/hud/hud.sh'):
            (self.repo / 'agterm-linux/.build/release' / name).write_text('fixture')
        for name in ('zmx', 'LICENSE'):
            (self.repo / 'agterm-linux/vendor/zmx' / name).write_text('fixture')
        shutil.copy2(ROOT / 'packaging/linux/io.github.melonamin.agterm.desktop', self.repo / 'packaging/linux')
        self.compat = self.work / 'agterm/swift-compat'
        self.compat.mkdir(parents=True)
        (self.compat / 'libncurses.so.6').write_text('real compatible library')
        self.bin = self.work / 'bin'
        self.bin.mkdir()
        ldd = self.bin / 'ldd'
        ldd.write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_DEPENDENCIES"\n')
        ldd.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        AGTERM_PACKAGE_COMMIT='fixture',
                        FAKE_DEPENDENCIES=f'libncurses.so.6 => {self.compat}/libncurses.so.6 (0x123)')
        self.destination = self.work / 'payload'

    def stage(self):
        return subprocess.run(['bash', str(self.repo / 'scripts/stage-linux.sh'), str(self.destination)],
                              env=self.env, text=True, capture_output=True)

    def test_bundles_compatibility_library(self):
        result = self.stage()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shutil.rmtree(self.repo)
        shutil.rmtree(self.compat)
        self.assertEqual((self.destination / 'lib/libncurses.so.6').read_text(), 'real compatible library')
        self.assertTrue((self.destination / 'bin/zmx').is_file())

    def test_rejects_missing_runtime_library(self):
        self.env['FAKE_DEPENDENCIES'] += '\nlibxml2.so.2 => not found'
        result = self.stage()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot stage unresolved shared libraries:', result.stderr)
        self.assertFalse((self.destination / 'bin/agterm-linux').exists())


if __name__ == '__main__':
    unittest.main()
