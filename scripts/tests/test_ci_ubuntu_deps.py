import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'ci-install-ubuntu-deps.sh'


class UbuntuDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.release = self.root / 'os-release'
        self.release.write_text('ID=ubuntu\nVERSION_CODENAME=noble\n')
        self.script = self.root / 'install.sh'
        self.script.write_text(SOURCE.read_text().replace('/etc/os-release', str(self.release)))
        self.log = self.root / 'calls.jsonl'
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
                        CALL_LOG=str(self.log), TEST_ARCH='amd64', TMPDIR=str(self.root))
        self.executable('dpkg', '#!/bin/sh\nprintf "%s\\n" "$TEST_ARCH"\n')
        self.executable('apt-get', f'#!{shutil.which("python3")}\n' + '''
import json, os, pathlib, sys
args = sys.argv[1:]
source = next(arg.split('=', 1)[1] for arg in args if arg.startswith('Dir::Etc::sourcelist='))
lists = pathlib.Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('Dir::State::lists=')))
archives = pathlib.Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('Dir::Cache::archives=')))
phase = 'update' if 'update' in args else 'download' if '--download-only' in args else 'configure'
text = pathlib.Path(source).read_text()
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps({'args': args, 'source': source, 'text': text,
                         'indexes': sorted(p.name for p in lists.iterdir()),
                         'archives': sorted(p.name for p in archives.iterdir()), 'phase': phase}) + '\\n')
failed = (os.environ.get('FAIL_COMMAND') == phase or
          ('update' in args and os.environ.get('FAIL_PRIMARY') == '1' and 'kernel.org' in text) or
          (phase == 'download' and os.environ.get('FAIL_PRIMARY_DOWNLOAD') == '1' and 'kernel.org' in text))
if 'update' in args:
    (lists / ('partial-index' if failed else 'verified-index')).write_text(text)
if phase == 'download':
    (archives / ('partial-package' if failed else 'verified-package')).write_text(text)
sys.exit(100 if failed else 0)
''')

    def executable(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def run_install(self, *packages):
        return subprocess.run(['bash', str(self.script), *packages], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_architectures_share_verified_sources_for_update_and_install(self):
        for arch, uri in [('amd64', 'https://mirrors.edge.kernel.org/ubuntu'),
                          ('arm64', 'https://mirrors.mit.edu/ubuntu-ports')]:
            with self.subTest(arch=arch):
                self.log.unlink(missing_ok=True)
                self.env['TEST_ARCH'] = arch
                result = self.run_install('fish', 'libgtk-4-dev')
                self.assertEqual(result.returncode, 0, result.stderr)
                update, download, install = self.calls()
                self.assertEqual(update['indexes'], [])
                self.assertEqual(install['indexes'], ['verified-index'])
                self.assertEqual(update['source'], install['source'])
                self.assertEqual(update['text'], install['text'])
                self.assertIn(f'URIs: {uri}\n', update['text'])
                self.assertIn('Suites: noble noble-updates noble-security noble-backports\n', update['text'])
                self.assertIn('Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n', update['text'])
                for call in (update, download, install):
                    self.assertIn('Dir::Etc::sourceparts=-', call['args'])
                    self.assertIn('APT::Update::Error-Mode=any', call['args'])
                    lists = next(arg.split('=', 1)[1] for arg in call['args']
                                 if arg.startswith('Dir::State::lists='))
                    self.assertEqual(Path(lists).parent, Path(call['source']).parent)
                    archives = next(arg.split('=', 1)[1] for arg in call['args']
                                    if arg.startswith('Dir::Cache::archives='))
                    self.assertEqual(Path(archives).parent, Path(call['source']).parent)
                self.assertEqual(download['args'][-6:],
                                 ['install', '--download-only', '-y', '--no-install-recommends', 'fish', 'libgtk-4-dev'])
                self.assertEqual(install['args'][-6:],
                                 ['install', '--no-download', '-y', '--no-install-recommends', 'fish', 'libgtk-4-dev'])
                self.assertEqual([c['phase'] for c in self.calls()], ['update', 'download', 'configure'])
                self.assertEqual(download['archives'], [])
                self.assertEqual(install['archives'], ['verified-package'])
                self.assertEqual(download['source'], install['source'])
                self.assertFalse(Path(update['source']).parent.exists())

    def test_failed_update_never_installs_or_leaves_sources(self):
        self.env['FAIL_COMMAND'] = 'update'
        self.assertEqual(self.run_install('fish').returncode, 100)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len({call['source'] for call in calls}), 2)
        for call in calls:
            self.assertEqual(call['args'][-1], 'update')
            self.assertEqual(call['indexes'], [])
            self.assertFalse(Path(call['source']).parent.exists())

    def test_fallback_uses_fresh_indexes_and_only_installs_from_successful_source(self):
        self.env['FAIL_PRIMARY'] = '1'
        result = self.run_install('fish')
        self.assertEqual(result.returncode, 0, result.stderr)
        primary, fallback, download, install = self.calls()
        self.assertIn('URIs: https://mirrors.edge.kernel.org/ubuntu\n', primary['text'])
        self.assertIn('URIs: https://mirrors.mit.edu/ubuntu\n', fallback['text'])
        self.assertNotEqual(primary['source'], fallback['source'])
        self.assertEqual(primary['indexes'], [])
        self.assertEqual(fallback['indexes'], [])
        self.assertEqual(install['indexes'], ['verified-index'])
        self.assertEqual(fallback['source'], install['source'])
        self.assertEqual(fallback['args'][:-1], download['args'][:-5])
        self.assertEqual(fallback['args'][:-1], install['args'][:-5])
        self.assertEqual(download['archives'], [])
        self.assertEqual(install['archives'], ['verified-package'])
        self.assertEqual(primary['text'].replace('https://mirrors.edge.kernel.org/ubuntu',
                                               'https://mirrors.mit.edu/ubuntu'), fallback['text'])
        self.assertIn('index update failed', result.stderr)
        for call in (primary, fallback, download, install):
            self.assertFalse(Path(call['source']).parent.exists())

    def test_arm_update_failure_has_no_unverified_fallback(self):
        self.env.update(TEST_ARCH='arm64', FAIL_COMMAND='update')
        self.assertEqual(self.run_install('fish').returncode, 100)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertIn('URIs: https://mirrors.mit.edu/ubuntu-ports\n', calls[0]['text'])
        self.assertFalse(Path(calls[0]['source']).parent.exists())

    def test_failed_install_propagates_and_cleans_sources(self):
        self.env['FAIL_COMMAND'] = 'configure'
        self.assertEqual(self.run_install('fish').returncode, 100)
        update, download, install = self.calls()
        self.assertEqual(update['source'], install['source'])
        self.assertFalse(Path(install['source']).parent.exists())

    def test_failed_fallback_install_is_not_retried(self):
        self.env.update(FAIL_PRIMARY='1', FAIL_COMMAND='configure')
        self.assertEqual(self.run_install('fish').returncode, 100)
        primary, fallback, download, install = self.calls()
        self.assertEqual(fallback['source'], install['source'])
        self.assertEqual(install['indexes'], ['verified-index'])
        for call in (primary, fallback, download, install):
            self.assertFalse(Path(call['source']).parent.exists())

    def test_download_failure_falls_back_before_any_configuration(self):
        self.env['FAIL_PRIMARY_DOWNLOAD'] = '1'
        result = self.run_install('fish')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([c['phase'] for c in calls],
                         ['update', 'download', 'update', 'download', 'configure'])
        primary, failed, fallback, download, install = calls
        self.assertEqual(primary['source'], failed['source'])
        self.assertNotEqual(primary['source'], fallback['source'])
        self.assertIn('URIs: https://mirrors.mit.edu/ubuntu\n', fallback['text'])
        self.assertEqual(fallback['indexes'], [])
        self.assertEqual(fallback['archives'], [])
        self.assertEqual(download['indexes'], ['verified-index'])
        self.assertEqual(download['archives'], [])
        self.assertEqual(download['source'], install['source'])
        self.assertEqual(install['archives'], ['verified-package'])
        self.assertIn('download failed', result.stderr)
        for call in calls:
            self.assertFalse(Path(call['source']).parent.exists())

    def test_all_downloads_fail_without_configuration(self):
        for arch, attempts in [('amd64', 2), ('arm64', 1)]:
            with self.subTest(arch=arch):
                self.log.unlink(missing_ok=True)
                self.env.update(TEST_ARCH=arch, FAIL_COMMAND='download')
                self.assertEqual(self.run_install('fish').returncode, 100)
                calls = self.calls()
                self.assertEqual([c['phase'] for c in calls], ['update', 'download'] * attempts)
                self.assertEqual(len({c['source'] for c in calls}), attempts)
                for call in calls:
                    self.assertEqual(call['archives'], [])
                    self.assertFalse(Path(call['source']).parent.exists())

    def test_configuration_failure_after_download_fallback_is_not_retried(self):
        self.env.update(FAIL_PRIMARY_DOWNLOAD='1', FAIL_COMMAND='configure')
        self.assertEqual(self.run_install('fish').returncode, 100)
        calls = self.calls()
        self.assertEqual([c['phase'] for c in calls],
                         ['update', 'download', 'update', 'download', 'configure'])
        self.assertEqual(calls[-1]['archives'], ['verified-package'])
        for call in calls:
            self.assertFalse(Path(call['source']).parent.exists())

    def test_unsupported_release_architecture_and_empty_request_fail_before_apt(self):
        for release, arch, packages in [('ID=debian\nVERSION_CODENAME=noble\n', 'amd64', ['fish']),
                                        ('ID=ubuntu\nVERSION_CODENAME=jammy\n', 'amd64', ['fish']),
                                        ('ID=ubuntu\nVERSION_CODENAME=noble\n', 'riscv64', ['fish']),
                                        ('ID=ubuntu\nVERSION_CODENAME=noble\n', 'amd64', [])]:
            with self.subTest(release=release, arch=arch, packages=packages):
                self.release.write_text(release)
                self.env['TEST_ARCH'] = arch
                self.assertNotEqual(self.run_install(*packages).returncode, 0)
                self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
