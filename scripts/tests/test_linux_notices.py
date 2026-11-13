#!/usr/bin/env python3
"""Notice provenance and stale-cache fixtures; never build or run a candidate binary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        (self.repo / 'linux').mkdir()
        for name in ['linux-notices.py', 'verify-linux-vendor-cache.sh', 'setup-linux.sh']:
            shutil.copy2(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        for name in ['ghostty-resources.env', 'arch.sh', 'zmx.env']:
            shutil.copy2(ROOT / 'linux' / name, self.repo / 'linux' / name)
        shutil.copytree(ROOT / 'linux/patches', self.repo / 'linux/patches')
        shutil.copytree(ROOT / 'scripts/zmx-patches', self.repo / 'scripts/zmx-patches')
        shutil.copytree(ROOT / 'packaging/linux/notices', self.repo / 'packaging/linux/notices')
        shutil.copy2(ROOT / 'LICENSE', self.repo / 'LICENSE')
        self.notices = self.repo / 'packaging/linux/notices'
        self.vendor = self.repo / 'agterm-linux/vendor/ghostty'
        self.cache = self.vendor / 'share/licenses/ghostty'
        for name in ['lib/libghostty.so', 'include/ghostty.h', 'include/ghostty/vt.h']:
            path = self.vendor / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture')
        zmx = self.repo / 'agterm-linux/vendor/zmx'
        zmx.mkdir(parents=True); (zmx / 'LICENSE').write_text('zmx fixture license\n')
        self.destination = self.root / 'stage'
        (self.destination / 'lib').mkdir(parents=True)
        (self.destination / 'lib/libswiftCore.so').write_bytes(b'fixture runtime')
        self.command('cache-ghostty', self.cache, self.notices / 'ghostty')
        stamp = subprocess.run(['bash', str(self.repo / 'scripts/verify-linux-vendor-cache.sh'), '--print-stamp'],
                               text=True, capture_output=True, check=True, timeout=10).stdout
        (self.vendor / 'SOURCE_STAMP').write_text(stamp)

    def command(self, *arguments, success=True):
        result = subprocess.run([sys.executable, str(self.repo / 'scripts/linux-notices.py'), *map(str, arguments)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_pinned_profile_and_cache_verify(self):
        self.command('verify-profile')
        self.command('verify-ghostty', self.cache)

    def test_cache_without_current_patch_inputs_is_rejected(self):
        path = self.cache / 'provenance.json'
        value = json.loads(path.read_text())
        del value['linux_patch_inputs']
        path.write_text(json.dumps(value))
        self.assertIn('stale Ghostty cache notice provenance',
                      self.command('verify-ghostty', self.cache, success=False).stderr)

    def test_patch_change_requires_profile_and_new_cache(self):
        self.check_patch_change('ghostty-clipboard-request-kind.patch')

    def test_environment_unset_patch_change_requires_new_cache(self):
        self.check_patch_change('ghostty-surface-env-unset.patch')

    def check_patch_change(self, name):
        patch = self.repo / 'linux/patches' / name
        patch.write_bytes(patch.read_bytes() + b'\n')
        self.assertIn('stale or changed Ghostty patch inputs',
                      self.command('verify-profile', success=False).stderr)
        profile_path = self.notices / 'sources.json'
        profile = json.loads(profile_path.read_text())
        profile['ghostty_patch_inputs'][str(patch.relative_to(self.repo))] = hashlib.sha256(patch.read_bytes()).hexdigest()
        profile_path.write_text(json.dumps(profile))
        self.command('verify-profile')
        self.assertIn('stale Ghostty cache notice provenance',
                      self.command('verify-ghostty', self.cache, success=False).stderr)
        shutil.rmtree(self.cache)
        self.command('cache-ghostty', self.cache, self.notices / 'ghostty')
        self.command('verify-ghostty', self.cache)

    def test_missing_empty_changed_and_linked_cache_license_fail(self):
        license_path = self.cache / 'LICENSE'; original = license_path.read_bytes()
        for kind in ['missing', 'empty', 'changed', 'symlink']:
            with self.subTest(kind=kind):
                license_path.unlink()
                if kind == 'empty': license_path.write_bytes(b'')
                elif kind == 'changed': license_path.write_bytes(b'wrong notice')
                elif kind == 'symlink': license_path.symlink_to(self.notices / 'ghostty/LICENSE')
                self.command('verify-ghostty', self.cache, success=False)
                if license_path.exists() or license_path.is_symlink(): license_path.unlink()
                license_path.write_bytes(original)

    def test_stale_cache_provenance_is_rejected(self):
        path = self.cache / 'provenance.json'; value = json.loads(path.read_text())
        value['revision'] = '0' * 40; path.write_text(json.dumps(value))
        self.assertIn('stale Ghostty cache notice provenance', self.command('verify-ghostty', self.cache, success=False).stderr)

    def test_source_license_mismatch_never_creates_cache(self):
        source = self.root / 'wrong-source'; source.mkdir(); (source / 'LICENSE').write_text('wrong')
        target = self.root / 'new-cache'
        self.command('cache-ghostty', target, source, success=False)
        self.assertFalse(target.exists())

    def test_profile_pin_must_follow_ghostty_source(self):
        (self.repo / 'linux/ghostty-resources.env').write_text('GHOSTTY_REV="' + '0' * 40 + '"\n')
        self.assertIn('stale Ghostty notice revision', self.command('verify-profile', success=False).stderr)

    def test_missing_or_tampered_swift_notice_rejects_staging(self):
        path = self.notices / 'swift-foundation/NOTICE.txt'
        original = path.read_bytes()
        path.unlink(); self.command('stage', self.destination, success=False)
        self.assertFalse((self.destination / 'share/licenses/agterm').exists())
        path.write_bytes(original + b'changed')
        self.assertIn('notice digest mismatch', self.command('stage', self.destination, success=False).stderr)
        self.assertFalse((self.destination / 'share/licenses/agterm').exists())

    def test_immutable_url_must_match_revision(self):
        path = self.notices / 'sources.json'; value = json.loads(path.read_text())
        value['files'][0]['url'] = value['files'][0]['url'].replace(value['files'][0]['revision'], 'main')
        path.write_text(json.dumps(value))
        self.assertIn('URL does not match', self.command('verify-profile', success=False).stderr)

    def test_missing_repository_license_rejects_staging(self):
        (self.repo / 'LICENSE').unlink()
        self.command('stage', self.destination, success=False)
        self.assertFalse((self.destination / 'share/licenses/agterm').exists())

    def test_staging_keeps_exact_notices_after_source_is_removed(self):
        source = (self.notices / 'sources.json').read_bytes()
        self.command('stage', self.destination)
        shutil.rmtree(self.repo)
        target = self.destination / 'share/licenses/agterm'
        self.assertEqual((target / 'sources.json').read_bytes(), source)
        for entry in json.loads(source)['files']:
            self.assertEqual(hashlib.sha256((target / entry['file']).read_bytes()).hexdigest(), entry['sha256'])
        manifest = json.loads((target / 'payload.json').read_text())
        self.assertEqual(manifest['source_profile_sha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(manifest['bundled_libraries'], [{'file': 'libswiftCore.so', 'sha256': hashlib.sha256(b'fixture runtime').hexdigest()}])
        self.assertEqual((target / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())

    def test_staging_refuses_stale_vendor_before_writing_notices(self):
        (self.cache / 'LICENSE').write_bytes(b'stale')
        self.command('stage', self.destination, success=False)
        self.assertFalse((self.destination / 'share/licenses/agterm').exists())

    @unittest.skipUnless(shutil.which('tic') and shutil.which('infocmp'), 'terminfo tools unavailable')
    def test_real_resource_verifier_with_adjacent_notices(self):
        share = self.vendor / 'share'
        ghostty = share / 'ghostty'
        for name in ['shell-integration/bash/ghostty.bash', 'shell-integration/zsh/ghostty-integration',
                     'shell-integration/zsh/.zshenv']:
            path = ghostty / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture')
        themes = ghostty / 'themes'; themes.mkdir()
        for name in ['Builtin Dark', 'Catppuccin Mocha', 'Dracula'] + [f'Fixture {i}' for i in range(599)]:
            (themes / name).write_text('background = 000000\n')
        shutil.copy2(ROOT / 'linux/ghostty-resources.env', themes / '.agterm-resource-manifest')
        terminfo = self.root / 'ghostty.terminfo'
        terminfo.write_text('xterm-ghostty|notice fixture,\n\tuse=xterm-256color,\n')
        subprocess.run(['tic', '-x', '-o', str(share / 'terminfo'), str(terminfo)], check=True, capture_output=True)
        # macOS tic uses a hex directory; retain it for host infocmp and also supply the Linux layout.
        linux_entry = share / 'terminfo/x/xterm-ghostty'
        if not linux_entry.exists():
            compiled = next((share / 'terminfo').rglob('xterm-ghostty'))
            linux_entry.parent.mkdir()
            shutil.copy2(compiled, linux_entry)
        command = ['bash', str(ROOT / 'scripts/verify-linux-resources.sh'), str(share)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (ghostty / 'shell-integration/zsh/.zshenv').unlink()
        self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 0)

    def update_inventory(self, change):
        path = self.notices / 'inventory.json'
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))
        profile = self.notices / 'sources.json'
        manifest = json.loads(profile.read_text())
        manifest['inventory_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        profile.write_text(json.dumps(manifest))

    def test_inventory_hash_is_checked_before_staging(self):
        path = self.notices / 'inventory.json'
        path.write_bytes(path.read_bytes() + b' ')
        result = self.command('stage', self.destination, success=False)
        self.assertIn('inventory digest mismatch', result.stderr)
        self.assertFalse((self.destination / 'share/licenses/agterm').exists())

    def test_archive_declaration_must_match_retained_lock(self):
        self.update_inventory(lambda value: value['archives']['libxev'].update(zig_hash='incorrect'))
        self.assertIn('pinned dependency manifest', self.command('verify-profile', success=False).stderr)

    def test_theme_source_pin_is_not_silently_replaced(self):
        self.update_inventory(lambda value: value['archives']['iterm2_themes'].update(sha256='0' * 64))
        self.assertIn('stale theme notice archive identity', self.command('verify-profile', success=False).stderr)

    def test_theme_inventory_cannot_lose_or_duplicate_a_file(self):
        path = self.notices / 'inventory.json'
        original = path.read_bytes()
        for operation in ['missing', 'duplicate']:
            with self.subTest(operation=operation):
                path.write_bytes(original)
                def change(value):
                    entries = value['themes']['files']
                    if operation == 'missing': entries.pop()
                    else: entries[-1] = entries[0]
                self.update_inventory(change)
                self.command('verify-profile', success=False)

    def test_component_cannot_reference_missing_notice(self):
        self.update_inventory(lambda value: value['components']['z2d']['notices'].append('z2d/MISSING'))
        self.assertIn('missing retained notices', self.command('verify-profile', success=False).stderr)

    def test_unmapped_compatibility_library_is_explicitly_unresolved(self):
        (self.destination / 'lib/libncurses.so.6').write_bytes(b'unreviewed compatibility library')
        self.command('stage', self.destination)
        payload = json.loads((self.destination / 'share/licenses/agterm/payload.json').read_text())
        self.assertEqual(payload['unresolved_libraries'], ['libncurses.so.6'])
        self.assertEqual(payload['library_notice_components']['libncurses.so.6'], [])
        self.assertEqual(payload['library_notice_components']['libswiftCore.so'], ['swift'])

    def test_linked_component_directory_is_rejected(self):
        original = self.notices / 'z2d'
        moved = self.root / 'external-z2d'
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        self.assertIn('linked notice directory', self.command('verify-profile', success=False).stderr)

    def test_archive_excerpt_and_member_metadata_are_checked(self):
        path = self.notices / 'sources.json'
        original = path.read_bytes()
        for operation in ['length', 'traversal', 'unknown-archive']:
            with self.subTest(operation=operation):
                manifest = json.loads(original)
                entry = next(entry for entry in manifest['files'] if 'archive' in entry and 'excerpt' in entry)
                if operation == 'length': entry['excerpt']['end'] += 1
                elif operation == 'traversal': entry['source_path'] = '../notice'
                else: entry['archive'] = 'unknown'
                path.write_text(json.dumps(manifest))
                self.command('verify-profile', success=False)

    def test_staged_inventory_and_runtime_mapping_survive_source_removal(self):
        original = (self.notices / 'inventory.json').read_bytes()
        self.command('stage', self.destination)
        shutil.rmtree(self.repo)
        staged = self.destination / 'share/licenses/agterm/inventory.json'
        self.assertEqual(staged.read_bytes(), original)
        inventory = json.loads(original)
        self.assertIn('swift', inventory['bundled_libraries']['libswiftCore.so'])
        self.assertEqual(len(inventory['bundled_libraries']), 16)
        self.assertEqual(len(inventory['themes']['files']), 602)
        self.assertTrue(all(theme['attribution'] == 'unresolved-individual-author-license'
                            for theme in inventory['themes']['files']))

    def setup_fixture(self):
        # Architecture/resource behavior is covered separately; these fixtures isolate notice-driven cache reuse.
        for name in ['verify-linux-architecture.sh', 'verify-linux-resources.sh', 'verify-linux-zmx-cache.sh']:
            path = self.repo / 'scripts' / name; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
        executable = self.root / 'bin'; executable.mkdir()
        for name, content in {'zig': 'echo 0.16.0', 'mise': 'exit 1', 'uname': 'echo aarch64',
                              'file': 'exit 98', 'git': 'echo notice-fixture-rebuild >&2; exit 97'}.items():
            path = executable / name; path.write_text('#!/bin/sh\n' + content + '\n'); path.chmod(0o755)
        return dict(os.environ, PATH=str(executable) + ':' + os.environ['PATH'], TMPDIR=str(self.root))

    def test_setup_reuses_complete_notice_cache(self):
        result = subprocess.run(['bash', str(self.repo / 'scripts/setup-linux.sh')], env=self.setup_fixture(),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already vendored', result.stdout)

    def test_setup_enters_rebuild_path_for_legacy_cache_without_notice(self):
        (self.cache / 'LICENSE').unlink()
        result = subprocess.run(['bash', str(self.repo / 'scripts/setup-linux.sh')], env=self.setup_fixture(),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertIn('notice-fixture-rebuild', result.stderr)
        self.assertFalse((self.cache / 'LICENSE').exists(), 'failed rebuild must not rewrite the old cache')


if __name__ == '__main__':
    unittest.main()
