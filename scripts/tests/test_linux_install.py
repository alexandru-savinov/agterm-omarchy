#!/usr/bin/env python3
"""Exercise the installer with a staged payload, without Swift or a graphical session."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
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
        self.env.pop('XDG_CONFIG_HOME', None)
        self.stage = self.repo / 'scripts/stage-linux.sh'
        self.stage.write_text('''#!/usr/bin/env bash
set -eu
[[ "${FAIL_STAGE:-0}" != 1 ]] || exit 42
mkdir -p "$1/bin" "$1/lib" "$1/share/agterm/agent-skill" "$1/share/agterm/agent-status" \
  "$1/share/ghostty/shell-integration" "$1/share/terminfo/x" "$1/share/applications" \
  "$1/share/pixmaps" "$1/share/icons/hicolor/scalable/actions"
for name in agterm-linux agterm-linux.bin agtermctl agtermctl.bin zmx; do
  printf '#!/bin/sh\\nprintf fixture\\n' > "$1/bin/$name"
  chmod 755 "$1/bin/$name"
done
printf library > "$1/lib/libswiftCore.so"
printf theme > "$1/share/ghostty/theme"
printf entry > "$1/share/terminfo/x/xterm-ghostty"
printf icon > "$1/share/pixmaps/io.github.melonamin.agterm.png"
printf icon > "$1/share/icons/hicolor/scalable/actions/agterm-quick-symbolic.svg"
[[ "${OLD_ICON:-0}" != 1 ]] || printf icon > "$1/share/icons/hicolor/scalable/actions/agterm-old-symbolic.svg"
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
        self.install('--no-build', '--software-rendering')
        marker = self.prefix / 'lib/agterm/keep'
        marker.write_text('old installation')
        self.env['FAIL_STAGE'] = '1'
        self.install('--no-build', '--default-rendering', success=False)
        self.assertEqual(marker.read_text(), 'old installation')
        self.assertEqual((self.prefix / 'lib/agterm/share/agterm/RENDERER').read_text(), 'software\n')
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

    def test_renderer_default_selection_preservation_and_reset(self):
        marker = self.prefix / 'lib/agterm/share/agterm/RENDERER'
        self.install('--no-build')
        self.assertEqual(marker.read_text(), 'default\n')
        # An installation predating the marker keeps normal renderer behavior on upgrade.
        marker.unlink()
        self.install('--no-build')
        self.assertEqual(marker.read_text(), 'default\n')
        self.install('--no-build', '--software-rendering')
        self.assertEqual(marker.read_text(), 'software\n')
        self.install('--no-build')
        self.assertEqual(marker.read_text(), 'software\n')
        self.install('--no-build', '--default-rendering')
        self.assertEqual(marker.read_text(), 'default\n')
        self.install('--no-build')
        self.assertEqual(marker.read_text(), 'default\n')

    def test_invalid_renderer_marker_is_not_executed_or_silently_replaced(self):
        self.install('--no-build')
        marker = self.prefix / 'lib/agterm/share/agterm/RENDERER'
        injected = self.work / 'must-not-exist'
        content = f'software\ntouch "{injected}"\n'
        marker.write_text(content)
        result = self.install('--no-build', success=False)
        self.assertIn('invalid installed renderer mode', result.stderr)
        self.assertEqual(marker.read_text(), content)
        self.assertFalse(injected.exists())
        self.install('--no-build', '--default-rendering')
        self.assertEqual(marker.read_text(), 'default\n')

    def test_conflicting_renderer_options_do_not_create_install(self):
        self.install('--no-build', '--software-rendering', '--default-rendering', success=False)
        self.assertFalse(self.prefix.exists())

    def files(self):
        return sorted(str(path.relative_to(self.prefix)) for path in self.prefix.rglob('*'))

    def test_uninstall_leaves_the_prefix_empty(self):
        self.install('--no-build')
        for cache in ('share/icons/hicolor/icon-theme.cache', 'share/applications/mimeinfo.cache'):
            (self.prefix / cache).write_text('cache')
        self.assertIn('share/agterm/INSTALLED_FILES', [str(path.relative_to(self.prefix / 'lib/agterm'))
                                                      for path in (self.prefix / 'lib/agterm').rglob('*')])
        result = self.install('--uninstall')
        self.assertEqual(self.files(), [])
        self.assertTrue(self.prefix.is_dir())
        self.assertIn('renderer choice is not kept', result.stdout)

    def test_uninstall_keeps_unrelated_files_state_and_config(self):
        self.install('--no-build')
        unrelated = ['share/pixmaps/unrelated.png', 'share/icons/hicolor/scalable/actions/other-symbolic.svg',
                     'share/icons/hicolor/icon-theme.cache', 'bin/other-tool']
        for name in unrelated:
            (self.prefix / name).write_text('preserve')
        state = Path(self.env['XDG_DATA_HOME']) / 'agterm'
        config = Path(self.env['HOME']) / '.config/agterm'
        for directory in (state, config):
            directory.mkdir(parents=True)
            (directory / 'keep').write_text('user')
        self.install('--uninstall')
        self.assertEqual(self.files(), sorted(['bin', 'share', 'share/icons', 'share/icons/hicolor',
                                               'share/icons/hicolor/scalable',
                                               'share/icons/hicolor/scalable/actions', 'share/pixmaps',
                                               *unrelated]))
        self.assertTrue((state / 'keep').exists())
        self.install('--no-build')
        result = self.install('--uninstall', '--purge-state')
        self.assertFalse(state.exists())
        self.assertTrue((config / 'keep').exists())
        self.assertIn(f'Kept configuration in {config}', result.stdout)

    def test_uninstall_of_an_install_without_manifest_uses_its_payload(self):
        self.install('--no-build')
        (self.prefix / 'lib/agterm/share/agterm/INSTALLED_FILES').unlink()
        self.install('--uninstall')
        self.assertEqual(self.files(), [])

    def test_uninstall_refuses_a_manifest_entry_outside_installer_paths(self):
        self.install('--no-build')
        outside = self.work / 'outside'
        outside.write_text('preserve')
        manifest = self.prefix / 'lib/agterm/share/agterm/INSTALLED_FILES'
        for entry in ('bin/../../outside', '/etc/passwd', 'lib/agterm/bin/zmx'):
            with self.subTest(entry=entry):
                manifest.write_text(f'bin/agtermctl\n{entry}\n')
                result = self.install('--uninstall', success=False)
                self.assertIn('refusing manifest entry', result.stderr)
                self.assertTrue((self.prefix / 'bin/agtermctl').is_symlink())
                self.assertTrue((self.prefix / 'lib/agterm/bin/zmx').exists())
        self.assertEqual(outside.read_text(), 'preserve')

    def test_uninstall_option_errors_change_nothing(self):
        for args in (('--uninstall',), ('--purge-state',), ('--uninstall', '--no-build'),
                     ('--uninstall', '--software-rendering'), ('--uninstall', '--payload', str(self.work))):
            with self.subTest(args=args):
                self.install(*args, success=False)
                self.assertFalse(self.prefix.exists())

    def test_reinstall_removes_icons_the_new_payload_dropped(self):
        old = self.prefix / 'share/icons/hicolor/scalable/actions/agterm-old-symbolic.svg'
        self.env['OLD_ICON'] = '1'
        self.install('--no-build')
        self.assertTrue(old.exists())
        del self.env['OLD_ICON']
        self.install('--no-build')
        self.assertFalse(old.exists())
        self.assertTrue((old.parent / 'agterm-quick-symbolic.svg').exists())

    def test_payload_installs_an_extracted_archive_without_building_or_staging(self):
        archive = self.work / 'extracted/agterm-stage'
        subprocess.run(['bash', str(self.stage), str(archive)], check=True)
        (archive / 'bin/agterm-linux').chmod(0o755)
        self.env.update(FAIL_STAGE='1', SWIFT=str(self.work / 'missing-swift'))
        self.install('--payload', str(archive), '--software-rendering')
        payload = self.prefix / 'lib/agterm'
        self.assertEqual((payload / 'bin/zmx').read_bytes(), (archive / 'bin/zmx').read_bytes())
        self.assertEqual((payload / 'share/agterm/RENDERER').read_text(), 'software\n')
        self.assertFalse((archive / 'share/agterm/RENDERER').exists())
        self.assertIn(f'Exec="{self.prefix}/bin/agterm-linux"\n',
                      (self.prefix / 'share/applications/io.github.melonamin.agterm.desktop').read_text())
        self.install('--uninstall')
        self.assertEqual(self.files(), [])

    def test_payload_must_be_an_agterm_payload(self):
        for args in (('--payload', str(self.work)), ('--payload',), ('--payload', str(self.work), '--no-build')):
            with self.subTest(args=args):
                self.install(*args, success=False)
                self.assertFalse(self.prefix.exists())

    def test_desktop_entry_gets_try_exec_in_the_main_group_only(self):
        self.install('--no-build')
        lines = (self.prefix / 'share/applications/io.github.melonamin.agterm.desktop').read_text().splitlines()
        self.assertEqual([line for line in lines if line.startswith('TryExec=')],
                         [f'TryExec={self.prefix}/bin/agterm-linux'])
        self.assertLess(lines.index(f'TryExec={self.prefix}/bin/agterm-linux'), lines.index('[Desktop Action QuickTerminal]'))

    def test_warns_when_the_prefix_is_off_the_xdg_data_path(self):
        warning = 'is not on XDG_DATA_HOME or XDG_DATA_DIRS'
        self.assertIn(warning, self.install('--no-build').stderr)
        self.env['XDG_DATA_DIRS'] = f'/usr/share:{self.prefix}/share'
        self.assertNotIn(warning, self.install('--no-build').stderr)
        del self.env['XDG_DATA_DIRS']
        self.env['XDG_DATA_HOME'] = str(self.prefix / 'share')
        self.assertNotIn(warning, self.install('--no-build').stderr)


class PayloadStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/stage-linux.sh', self.repo / 'scripts/stage-linux.sh')
        shutil.copy2(ROOT / 'scripts/linux-notices.py', self.repo / 'scripts/linux-notices.py')
        shutil.copytree(ROOT / 'packaging/linux/notices', self.repo / 'packaging/linux/notices')
        (self.repo / 'linux').mkdir()
        shutil.copy2(ROOT / 'linux/ghostty-resources.env', self.repo / 'linux/ghostty-resources.env')
        shutil.copy2(ROOT / 'LICENSE', self.repo / 'LICENSE')
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
            (self.repo / directory).mkdir(parents=True, exist_ok=True)
        for name in ('AgtermLinux', 'agtermctl-linux', 'agterm-linux_AgtermLinux.resources/hud/hud.sh'):
            (self.repo / 'agterm-linux/.build/release' / name).write_text('fixture')
        for name in ('zmx', 'LICENSE'):
            (self.repo / 'agterm-linux/vendor/zmx' / name).write_text('fixture')
        subprocess.run([sys.executable, str(self.repo / 'scripts/linux-notices.py'), 'cache-ghostty',
                        str(self.repo / 'agterm-linux/vendor/ghostty/share/licenses/ghostty'),
                        str(self.repo / 'packaging/linux/notices/ghostty')], check=True)
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
        notices = self.destination / 'share/licenses/agterm'
        self.assertEqual((notices / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())
        self.assertTrue((notices / 'swift-foundation/NOTICE.txt').is_file())
        self.assertTrue((notices / 'icu-76.1/LICENSE').is_file())
        provenance = json.loads((notices / 'payload.json').read_text())
        self.assertEqual(provenance['bundled_libraries'][0]['file'], 'libncurses.so.6')

    def test_rejects_missing_runtime_library(self):
        self.env['FAKE_DEPENDENCIES'] += '\nlibxml2.so.2 => not found'
        result = self.stage()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot stage unresolved shared libraries:', result.stderr)
        self.assertFalse((self.destination / 'bin/agterm-linux').exists())

    def stage_probe_wrappers(self):
        # The actual staging code writes both wrappers around harmless environment/argv probes.
        probe = f'#!{sys.executable}\n' + '''import json, os, sys
print(json.dumps({"renderer": os.environ.get("LIBGL_ALWAYS_SOFTWARE"), "args": sys.argv[1:]}))
'''
        for binary in ('AgtermLinux', 'agtermctl-linux'):
            (self.repo / 'agterm-linux/.build/release' / binary).write_text(probe)
        result = self.stage()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def launch_probe(self, name, *args, renderer='0', launcher=None):
        env = dict(self.env)
        env.pop('LIBGL_ALWAYS_SOFTWARE', None)
        if renderer is not None:
            env['LIBGL_ALWAYS_SOFTWARE'] = renderer
        result = subprocess.run([str(launcher or self.destination / 'bin' / name), *args],
                                env=env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_default_wrapper_preserves_inherited_renderer_and_arguments(self):
        self.stage_probe_wrappers()
        marker = self.destination / 'share/agterm/RENDERER'
        args = ('--desktop-action=new-window', 'path with spaces', '$(literal); argument')
        for mode in (None, 'default'):
            if mode:
                marker.write_text(mode + '\n')
            for renderer in (None, '0', '1'):
                for name in ('agterm-linux', 'agtermctl'):
                    with self.subTest(mode=mode, renderer=renderer, name=name):
                        self.assertEqual(self.launch_probe(name, *args, renderer=renderer),
                                         {'renderer': renderer, 'args': list(args)})

    def test_software_wrapper_survives_relocation_and_covers_desktop_actions(self):
        self.stage_probe_wrappers()
        (self.destination / 'share/agterm/RENDERER').write_text('software\n')
        relocated = self.work / 'relocated payload with spaces'
        self.destination.rename(relocated)
        self.destination = relocated
        shutil.rmtree(self.repo)
        launcher = self.work / 'command-link'
        launcher.symlink_to(os.path.relpath(self.destination / 'bin/agterm-linux', launcher.parent))
        self.assertEqual(self.launch_probe('agterm-linux', 'path with spaces', launcher=launcher),
                         {'renderer': '1', 'args': ['path with spaces']})
        desktop = self.destination / 'share/applications/io.github.melonamin.agterm.desktop'
        actions = [shlex.split(line[5:]) for line in desktop.read_text().splitlines()
                   if line.startswith('Exec=')]
        self.assertGreater(len(actions), 1)
        for executable, *args in actions:
            self.assertEqual(executable, 'agterm-linux')
            self.assertEqual(self.launch_probe(executable, *args), {'renderer': '1', 'args': args})
        self.assertEqual(self.launch_probe('agtermctl', 'tree'), {'renderer': '0', 'args': ['tree']})
        (self.destination / 'share/agterm/RENDERER').write_text('default\n')
        self.assertEqual(self.launch_probe('agterm-linux'), {'renderer': '0', 'args': []})

    LAUNCHER_VARIABLES = ('LD_LIBRARY_PATH', 'LIBGL_ALWAYS_SOFTWARE', 'AGTERM_ZMX_PATH',
                          'AGTERM_GHOSTTY_RESOURCES', 'AGTERM_VERSION', 'AGTERM_COMMIT')

    def launch_environment_probe(self, **caller):
        """Run the staged agterm-linux launcher over a binary that reports its whole environment."""
        probe = f'#!{sys.executable}\n' + 'import json, os\nprint(json.dumps(dict(os.environ)))\n'
        (self.destination / 'bin/agterm-linux.bin').write_text(probe)
        env = {key: value for key, value in self.env.items()
               if key not in self.LAUNCHER_VARIABLES and not key.startswith('AGTERM_ORIG_')}
        env.update(caller)
        result = subprocess.run([str(self.destination / 'bin/agterm-linux')], env=env, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def staged_software_launcher(self):
        self.stage_probe_wrappers()
        (self.destination / 'share/agterm/RENDERER').write_text('software\n')
        for name in ('VERSION', 'COMMIT'):
            (self.destination / 'share/agterm' / name).write_text('fixture\n')
        self.assertTrue((self.destination / 'share/ghostty').is_dir())

    def test_launcher_records_caller_values_it_overrides(self):
        # Issue #270: children must get these back, so the launcher has to say what they were.
        self.staged_software_launcher()
        caller = {name: f'caller {name} value' for name in self.LAUNCHER_VARIABLES}
        caller['LD_LIBRARY_PATH'] = '/home/user/lib:/opt/vendor lib'
        seen = self.launch_environment_probe(**caller)
        self.assertEqual(seen['LD_LIBRARY_PATH'], f'{self.destination}/lib:/home/user/lib:/opt/vendor lib')
        self.assertEqual(seen['LIBGL_ALWAYS_SOFTWARE'], '1')
        self.assertEqual(seen['AGTERM_ZMX_PATH'], f'{self.destination}/bin/zmx')
        for name, value in caller.items():
            with self.subTest(name=name):
                self.assertEqual(seen.get('AGTERM_ORIG_' + name), value)
        self.assertEqual(seen['AGTERM_ORIG_UNSET'], '')

    def test_launcher_lists_variables_the_caller_never_had(self):
        self.staged_software_launcher()
        seen = self.launch_environment_probe()
        self.assertEqual(seen['AGTERM_ORIG_UNSET'].split(' '), [
            'LD_LIBRARY_PATH', 'AGTERM_GHOSTTY_RESOURCES', 'AGTERM_VERSION', 'AGTERM_COMMIT',
            'AGTERM_ZMX_PATH', 'LIBGL_ALWAYS_SOFTWARE'])
        self.assertEqual([key for key in seen if key.startswith('AGTERM_ORIG_')], ['AGTERM_ORIG_UNSET'])
        self.assertEqual(seen['LD_LIBRARY_PATH'], f'{self.destination}/lib')

    def test_launcher_records_an_empty_caller_value_as_set(self):
        self.staged_software_launcher()
        seen = self.launch_environment_probe(LD_LIBRARY_PATH='', LIBGL_ALWAYS_SOFTWARE='')
        self.assertEqual(seen['AGTERM_ORIG_LD_LIBRARY_PATH'], '')
        self.assertEqual(seen['AGTERM_ORIG_LIBGL_ALWAYS_SOFTWARE'], '')
        self.assertNotIn('LD_LIBRARY_PATH', seen['AGTERM_ORIG_UNSET'].split(' '))

    def test_launcher_clears_bookkeeping_inherited_from_an_outer_agterm(self):
        self.staged_software_launcher()
        seen = self.launch_environment_probe(AGTERM_ORIG_STALE='outer', AGTERM_ORIG_UNSET='STALE_NAME',
                                             AGTERM_ORIG_LD_LIBRARY_PATH='/outer/lib')
        self.assertNotIn('AGTERM_ORIG_STALE', seen)
        self.assertNotIn('STALE_NAME', seen['AGTERM_ORIG_UNSET'].split(' '))
        self.assertNotIn('AGTERM_ORIG_LD_LIBRARY_PATH', seen)

    def test_default_launcher_leaves_renderer_out_of_the_bookkeeping(self):
        self.stage_probe_wrappers()
        for caller in ({}, {'LIBGL_ALWAYS_SOFTWARE': '0'}):
            with self.subTest(caller=caller):
                seen = self.launch_environment_probe(**caller)
                self.assertEqual(seen.get('LIBGL_ALWAYS_SOFTWARE'), caller.get('LIBGL_ALWAYS_SOFTWARE'))
                self.assertNotIn('AGTERM_ORIG_LIBGL_ALWAYS_SOFTWARE', seen)
                self.assertNotIn('LIBGL_ALWAYS_SOFTWARE', seen.get('AGTERM_ORIG_UNSET', '').split(' '))

    def test_wrapper_only_honors_exact_software_data_marker(self):
        self.stage_probe_wrappers()
        marker = self.destination / 'share/agterm/RENDERER'
        injected = self.work / 'must-not-exist'
        for content in ('software-extra\n', f'software\ntouch "{injected}"\n',
                        f'$(touch "{injected}")\n', ' software\n'):
            with self.subTest(content=content):
                marker.write_text(content)
                self.assertEqual(self.launch_probe('agterm-linux')['renderer'], '0')
                self.assertFalse(injected.exists())


if __name__ == '__main__':
    unittest.main()
