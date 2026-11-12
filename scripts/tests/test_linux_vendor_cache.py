#!/usr/bin/env python3
"""A vendored libghostty cache is reused only when its build stamp matches the current pins and patches."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class VendorCacheStampTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / 'checkout'
        for name in ('scripts/verify-linux-vendor-cache.sh', 'scripts/setup-linux.sh',
                     'linux/ghostty-resources.env', 'linux/zmx.env', 'linux/arch.sh'):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        shutil.copytree(ROOT / 'linux/patches', self.repo / 'linux/patches')
        shutil.copytree(ROOT / 'scripts/zmx-patches', self.repo / 'scripts/zmx-patches')
        # Notice, architecture and resource checks have their own suites; isolate the stamp decision.
        for name in ('verify-linux-architecture.sh', 'verify-linux-resources.sh', 'verify-linux-zmx-cache.sh'):
            self.stub(self.repo / 'scripts' / name, 'exit 0')
        (self.repo / 'scripts/linux-notices.py').write_text('')
        self.vendor = self.repo / 'agterm-linux/vendor/ghostty'
        for name in ('lib/libghostty.so', 'include/ghostty.h', 'include/ghostty/vt.h'):
            path = self.vendor / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        (self.vendor / 'SOURCE_STAMP').write_text(self.expected_stamp())
        self.bin = self.work / 'bin'
        self.bin.mkdir()
        for name, body in {'zig': 'echo 0.16.0', 'mise': 'exit 1', 'uname': 'echo x86_64',
                           'git': 'echo stamp-fixture-rebuild >&2; exit 97'}.items():
            self.stub(self.bin / name, body)
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', TMPDIR=str(self.work))

    @staticmethod
    def stub(path, body):
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def expected_stamp(self):
        # Independent statement of the stamp format: revision, each patch digest, then the resource pin.
        env = self.repo / 'linux/ghostty-resources.env'
        rev = next(line.split('"')[1] for line in env.read_text().splitlines() if line.startswith('GHOSTTY_REV='))
        lines = [f'ghostty-rev {rev}']
        for patch in sorted((self.repo / 'linux/patches').glob('*.patch')):
            lines.append(f'patch {patch.name} {hashlib.sha256(patch.read_bytes()).hexdigest()}')
        lines.append(f'resources {hashlib.sha256(env.read_bytes()).hexdigest()}')
        return '\n'.join(lines) + '\n'

    def verify(self, success=True, message=''):
        result = subprocess.run(['bash', str(self.repo / 'scripts/verify-linux-vendor-cache.sh'), str(self.vendor)],
                                env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        return result

    def setup(self):
        return subprocess.run(['bash', str(self.repo / 'scripts/setup-linux.sh')], env=self.env,
                              text=True, capture_output=True, timeout=30)

    def test_matching_stamp_is_accepted_and_reused(self):
        self.verify()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('already vendored', result.stdout)

    def test_cache_without_stamp_is_rejected(self):
        (self.vendor / 'SOURCE_STAMP').unlink()
        self.verify(False, 'libghostty cache has no build stamp')

    def test_changed_patch_rejects_cache_and_setup_rebuilds(self):
        patch = self.repo / 'linux/patches/ghostty-embedded-opengl.patch'
        patch.write_bytes(patch.read_bytes() + b'\n')
        self.verify(False, 'built from different Ghostty sources or patches')
        result = self.setup()
        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertIn('stamp-fixture-rebuild', result.stderr)
        # Setup names the reason once before rebuilding, instead of discarding the verifier's message.
        self.assertEqual(result.stderr.count('rebuilding vendored libghostty:'), 1, result.stderr)
        self.assertEqual(result.stderr.count('built from different Ghostty sources or patches'), 1, result.stderr)
        self.assertIn('ghostty-embedded-opengl.patch', result.stderr)

    def test_added_patch_rejects_cache(self):
        (self.repo / 'linux/patches/ghostty-new-fix.patch').write_text('--- a\n+++ b\n')
        self.verify(False, 'ghostty-new-fix.patch')

    def test_changed_revision_or_resource_pin_rejects_cache(self):
        env = self.repo / 'linux/ghostty-resources.env'
        original = env.read_text()
        for change in ('revision', 'resources'):
            with self.subTest(change=change):
                if change == 'revision':
                    rev = next(line.split('"')[1] for line in original.splitlines() if line.startswith('GHOSTTY_REV='))
                    env.write_text(original.replace(rev, '0' * 40))
                else:
                    env.write_text(original + '# changed\n')
                self.verify(False, 'built from different Ghostty sources or patches')
                env.write_text(original)

    def test_printed_stamp_matches_the_documented_format(self):
        result = subprocess.run(['bash', str(self.repo / 'scripts/verify-linux-vendor-cache.sh'), '--print-stamp'],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected_stamp())

    def test_missing_patch_directory_fails_closed(self):
        shutil.rmtree(self.repo / 'linux/patches')
        self.verify(False, 'no Ghostty patches found')


if __name__ == '__main__':
    unittest.main()
