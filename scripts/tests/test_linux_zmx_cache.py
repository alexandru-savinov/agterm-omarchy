#!/usr/bin/env python3
"""Verify zmx distribution policy and rebuild decisions without network or Linux binaries."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ZmxCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / 'checkout'
        for name in ('scripts/setup-linux.sh', 'scripts/verify-linux-zmx-cache.sh',
                     'scripts/verify-linux-architecture.sh', 'linux/arch.sh',
                     'linux/zmx.env', 'linux/ghostty-resources.env'):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        shutil.copytree(ROOT / 'scripts/zmx-patches', self.repo / 'scripts/zmx-patches')
        self.vendor = self.repo / 'agterm-linux/vendor/zmx'
        self.vendor.mkdir(parents=True)
        self.bin = self.work / 'bin'
        self.bin.mkdir()
        self.marker = self.work / 'helper-ran'
        self.build_log = self.work / 'build-arguments'
        self.env = dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin',
                        TMPDIR=str(self.work), TEST_HOST='aarch64',
                        TEST_FILE_ARCH='ARM aarch64', TEST_HELPER_MARKER=str(self.marker),
                        TEST_BUILD_LOG=str(self.build_log))
        self.write_script(self.bin / 'uname', 'printf "%s\\n" "$TEST_HOST"\n')
        if shutil.which('sha256sum', path=self.env['PATH']) is None:
            self.write_script(self.bin / 'sha256sum', 'exec shasum -a 256 "$@"\n')
        # Only file identification is doubled; the production architecture verifier still runs.
        self.write_script(self.bin / 'file',
                          'printf "ELF 64-bit LSB executable, %s\\n" "$TEST_FILE_ARCH"\n')
        self.helper = '''[[ "$1" == help && -d "$ZMX_DIR" ]] || exit 91
[[ "$ZMX_DIR" != "${HOME}/.local/share/zmx" ]] || exit 92
printf '%s\\n' "$ZMX_DIR" >> "$TEST_HELPER_MARKER"
'''
        self.write_script(self.vendor / 'zmx', self.helper)
        (self.vendor / 'LICENSE').write_text('fixture license\n')
        patch_dir = self.repo / 'scripts/zmx-patches'
        patches = sorted(patch_dir.glob('*.patch')) + sorted((patch_dir / 'ghostty').glob('*.patch'))
        digest = hashlib.sha256(b''.join(p.read_bytes() for p in patches)).hexdigest()[:16]
        pin = next(line.split('"')[1] for line in (self.repo / 'linux/zmx.env').read_text().splitlines()
                   if line.startswith('ZMX_REV='))
        (self.vendor / 'REVISION').write_text(f'{pin} {digest}\n')
        self.set_policy()

    @staticmethod
    def write_script(path, body):
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body)
        path.chmod(0o755)

    def set_policy(self, target=None, cpu='baseline'):
        target = target or f'{self.env["TEST_HOST"]}-linux-gnu.2.39'
        (self.vendor / 'BUILD_POLICY').write_text(f'target={target} cpu={cpu}\n')

    def verify(self, success=True, message=''):
        result = subprocess.run([str(self.repo / 'scripts/verify-linux-zmx-cache.sh'),
                                 str(self.vendor)], env=self.env, text=True, capture_output=True,
                                timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.marker.exists(), success, 'invalid cache must never execute its helper')
        if success:
            for directory in self.marker.read_text().splitlines():
                self.assertFalse(Path(directory).exists(), 'verification directory must be cleaned up')
        return result

    def test_accepts_baseline_for_each_architecture(self):
        for host, elf in (('x86_64', 'x86-64'), ('aarch64', 'ARM aarch64')):
            with self.subTest(host=host):
                self.env.update(TEST_HOST=host, TEST_FILE_ARCH=elf)
                self.set_policy()
                self.verify()
                self.marker.unlink()

    def test_rejects_legacy_cache_before_execution(self):
        (self.vendor / 'BUILD_POLICY').unlink()
        self.verify(False, 'missing zmx build policy')

    def test_rejects_incompatible_target_or_cpu_before_execution(self):
        for target, cpu in (('x86_64-linux-gnu.2.39', 'baseline'),
                            ('aarch64-linux-gnu.2.40', 'baseline'),
                            ('aarch64-linux-gnu.2.39', 'native'),
                            ('aarch64-linux-gnu.2.39', 'baseline+aes')):
            with self.subTest(target=target, cpu=cpu):
                self.set_policy(target, cpu)
                self.verify(False, 'zmx cache build policy does not match')

    def test_rejects_wrong_elf_even_with_correct_policy(self):
        self.env['TEST_FILE_ARCH'] = 'x86-64'
        self.verify(False, 'unexpected ELF architecture')

    def test_rejects_wrong_revision_before_execution(self):
        (self.vendor / 'REVISION').write_text('stale\n')
        self.verify(False, 'zmx cache revision does not match')

    def prepare_build(self):
        # A valid Ghostty cache must survive a zmx-only rebuild without fetching Ghostty.
        ghostty = self.repo / 'agterm-linux/vendor/ghostty'
        ghostty.mkdir()
        self.ghostty_marker = ghostty / 'retained-library'
        self.ghostty_marker.write_bytes(b'unchanged Ghostty')
        self.write_script(self.repo / 'scripts/verify-linux-vendor-cache.sh',
                          '[[ "$(cat "$1/retained-library")" == "unchanged Ghostty" ]]\n')
        self.write_script(self.bin / 'zig', '[[ "$1" == version ]]\nprintf "0.16.0\\n"\n')
        self.write_script(self.bin / 'git', '[[ "$1" == -C && "$3" == apply ]]\n')
        # Each fixture archive's bytes name its URL, so the recorded pins identify one exact download.
        self.write_script(self.bin / 'curl', '[[ "$1" == -fsSLo ]]\nprintf "archive %s\\n" "$3" > "$2"\n')
        self.tar_log = self.work / 'extracted'
        self.write_script(self.bin / 'tar', '''[[ "$1" == -xzf && "$3" == -C ]]
printf '%s\\n' "$4" >> "$TEST_TAR_LOG"
if [[ "$4" == */zmx ]]; then
  printf 'git+https://github.com/ghostty-org/ghostty#1111111111111111111111111111111111111111"\\n' > "$4/build.zig.zon"
  printf 'fixture license\\n' > "$4/LICENSE"
fi
''')
        self.write_script(self.repo / 'scripts/zig-build-linux.sh', '''printf '%s\\n' "$@" >> "$TEST_BUILD_LOG"
mkdir -p zig-out/bin
cat > zig-out/bin/zmx <<'HELPER'
#!/usr/bin/env bash
set -euo pipefail
''' + self.helper + '''HELPER
chmod 0755 zig-out/bin/zmx
''')
        self.env['TEST_TAR_LOG'] = str(self.tar_log)
        self.pin_archives()

    def pin_archives(self, zmx=None, ghostty=None, ghostty_rev='1' * 40):
        env = self.repo / 'linux/zmx.env'
        rev = next(line.split('"')[1] for line in env.read_text().splitlines() if line.startswith('ZMX_REV='))
        def digest(url):
            return hashlib.sha256(f'archive {url}\n'.encode()).hexdigest()
        zmx = zmx or digest(f'https://codeload.github.com/neurosnap/zmx/tar.gz/{rev}')
        ghostty = ghostty or digest('https://codeload.github.com/ghostty-org/ghostty/tar.gz/' + '1' * 40)
        lines = [line for line in env.read_text().splitlines()
                 if not line.startswith(('ZMX_SHA256=', 'ZMX_GHOSTTY_PIN=', 'ZMX_GHOSTTY_SHA256='))]
        lines += [f'ZMX_SHA256="{zmx}"', f'ZMX_GHOSTTY_PIN="{ghostty_rev}"', f'ZMX_GHOSTTY_SHA256="{ghostty}"']
        env.write_text('\n'.join(lines) + '\n')

    def setup_runtimes(self):
        result = subprocess.run([str(self.repo / 'scripts/setup-linux.sh')],
                                env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ghostty_marker.read_bytes(), b'unchanged Ghostty')
        return result

    def test_setup_rebuilds_legacy_zmx_with_baseline_and_retains_ghostty(self):
        self.prepare_build()
        for host, elf in (('x86_64', 'x86-64'), ('aarch64', 'ARM aarch64')):
            with self.subTest(host=host):
                self.env.update(TEST_HOST=host, TEST_FILE_ARCH=elf)
                (self.vendor / 'BUILD_POLICY').unlink()
                if self.build_log.exists():
                    self.build_log.unlink()
                self.setup_runtimes()
                args = self.build_log.read_text().splitlines()
                self.assertEqual(args, ['-Doptimize=ReleaseSafe',
                                        f'-Dtarget={host}-linux-gnu.2.39', '-Dcpu=baseline'])
                self.assertEqual((self.vendor / 'BUILD_POLICY').read_text(),
                                 f'target={host}-linux-gnu.2.39 cpu=baseline\n')
                self.assertIn('complete libghostty and zmx caches', self.setup_runtimes().stdout)
                self.assertEqual(self.build_log.read_text().splitlines(), args,
                                 'a matching cache must not rebuild')

    def failed_setup(self, message):
        (self.vendor / 'BUILD_POLICY').unlink()
        result = subprocess.run([str(self.repo / 'scripts/setup-linux.sh')],
                                env=self.env, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.build_log.exists(), 'unverified sources must never be built')
        self.assertFalse((self.vendor / 'BUILD_POLICY').exists(), 'a failed fetch must not restage zmx')
        return result

    def test_setup_rejects_zmx_archive_with_wrong_checksum_before_extracting(self):
        self.prepare_build()
        self.pin_archives(zmx='0' * 64)
        self.failed_setup('checksum mismatch for https://codeload.github.com/neurosnap/zmx/tar.gz/')
        self.assertFalse(self.tar_log.exists(), 'a mismatched archive must never be extracted')

    def test_setup_rejects_ghostty_dependency_with_wrong_checksum_before_extracting(self):
        self.prepare_build()
        self.pin_archives(ghostty='0' * 64)
        self.failed_setup('checksum mismatch for https://codeload.github.com/ghostty-org/ghostty/tar.gz/')
        self.assertEqual(len(self.tar_log.read_text().splitlines()), 1, 'only the verified zmx archive is extracted')

    def test_setup_rejects_unpinned_ghostty_dependency_revision(self):
        self.prepare_build()
        self.pin_archives(ghostty_rev='2' * 40)
        self.failed_setup('linux/zmx.env pins ' + '2' * 40)

    def test_setup_rejects_missing_archive_pin(self):
        self.prepare_build()
        env = self.repo / 'linux/zmx.env'
        env.write_text(''.join(line for line in env.read_text().splitlines(True)
                               if not line.startswith('ZMX_SHA256=')))
        self.failed_setup('no SHA-256 pin recorded')

    def test_repository_pins_are_well_formed(self):
        values = dict(line.split('=', 1) for line in (ROOT / 'linux/zmx.env').read_text().splitlines()
                      if line and not line.startswith('#'))
        for key in ('ZMX_SHA256', 'ZMX_GHOSTTY_SHA256'):
            self.assertRegex(values.get(key, ''), r'^"[0-9a-f]{64}"$', key)
        self.assertRegex(values.get('ZMX_GHOSTTY_PIN', ''), r'^"[0-9a-f]{40}"$')


if __name__ == '__main__':
    unittest.main()
