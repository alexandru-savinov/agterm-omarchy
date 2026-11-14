#!/usr/bin/env python3
"""Exercise the boundary gate in disposable Git repositories, including tool errors."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'check-linux-core-boundary.sh'


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        shutil.copy(SCRIPT, self.root / 'scripts' / SCRIPT.name)
        self.core = self.root / 'agtermCore/Sources/agtermCore'
        self.core.mkdir(parents=True)
        (self.core / 'File.swift').write_text('import Foundation\n')
        self.protected_path = 'agtermCore/Tests/agtermCoreTests/ConfigPathsTests.swift'
        self.protected = self.root / self.protected_path
        self.protected.parent.mkdir(parents=True)
        self.protected.write_text('// upstream fixture\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'fixture')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for tool in ('bash', 'git', 'dirname'):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.rg = shutil.which('rg')
        self.assertIsNotNone(self.rg, 'test suite requires real ripgrep')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, text=True)

    def run_gate(self, mode='real'):
        search = self.bin / 'rg'
        if search.exists() or search.is_symlink():
            search.unlink()
        if mode == 'real':
            search.symlink_to(self.rg)
        elif mode != 'missing':
            # Error each stage independently, including a partial result before failure.
            condition = {'imports': '[[ "$2" == *AppKit* ]]',
                         'symbols': '[[ "$2" == *ghostty* ]]',
                         'filter': '[[ "$1" == -v ]]'}[mode]
            import shlex
            search.write_text('#!/usr/bin/env bash\n'
                              f'if {condition}; then echo partial-output; exit 2; fi\n'
                              f'exec {shlex.quote(self.rg)} "$@"\n')
            search.chmod(0o755)
        return subprocess.run([str(self.bin / 'bash'), str(self.root / 'scripts' / SCRIPT.name), 'HEAD'],
                              env={**os.environ, 'PATH': str(self.bin)},
                              capture_output=True, text=True)

    def test_clean_tree_and_no_matches_pass(self):
        self.assertEqual(self.run_gate().returncode, 0)

    def test_missing_search_tool_fails(self):
        result = self.run_gate('missing')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_import_search_error_fails(self):
        result = self.run_gate('imports')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_symbol_search_error_fails(self):
        result = self.run_gate('symbols')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_comment_filter_error_fails(self):
        result = self.run_gate('filter')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_missing_core_directory_fails(self):
        shutil.rmtree(self.core)
        result = self.run_gate()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_prohibited_import_fails(self):
        (self.core / 'File.swift').write_text('import AppKit\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_prohibited_symbol_fails(self):
        (self.core / 'File.swift').write_text('let widget = gtk_window_new()\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_symbol_only_in_line_comment_passes(self):
        (self.core / 'File.swift').write_text('// gtk_window_new() is host-side\n')
        self.assertEqual(self.run_gate().returncode, 0)

    def fixture_exception(self):
        self.protected.write_text('// reviewed portable fixture\n')
        base = self.git('rev-parse', 'HEAD:' + self.protected_path).stdout.strip()
        fixture = self.git('hash-object', '--no-filters', str(self.protected)).stdout.strip()
        record = [self.protected_path, base, fixture,
                  'https://github.com/alexandru-savinov/agterm-omarchy/issues/343']
        path = self.root / 'packaging/linux/core-fixture-exceptions.tsv'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# exact reviewed fixture delta\n' + '\t'.join(record) + '\n')
        return path, record

    def test_unreviewed_protected_file_drift_fails(self):
        self.protected.write_text('// arbitrary downstream change\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_exact_reviewed_fixture_delta_passes(self):
        self.fixture_exception()
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('exact reviewed fixture exception', result.stdout)

    def test_further_fixture_content_change_fails(self):
        self.fixture_exception()
        self.assertEqual(self.run_gate().returncode, 0)
        self.protected.write_text('// changed assertion or fixture after review\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_wrong_upstream_blob_fails(self):
        path, record = self.fixture_exception()
        record[1] = '0' * 40
        path.write_text('\t'.join(record) + '\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_other_path_is_not_an_exception(self):
        path, record = self.fixture_exception()
        record[0] = 'agtermCore/Sources/agtermCore/File.swift'
        path.write_text('\t'.join(record) + '\n')
        self.assertEqual(self.run_gate().returncode, 2)

    def test_malformed_or_duplicate_exception_fails_closed(self):
        path, record = self.fixture_exception()
        good = '\t'.join(record)
        malformed = [good + '\textra', good + '\t', '\t'.join(record[:3]),
                     good.replace(record[1], '*'), good + '\n' + good,
                     good.replace(record[3], 'missing-review-reference')]
        for content in malformed:
            with self.subTest(content=content):
                path.write_text(content + '\n')
                result = self.run_gate()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_exception_does_not_allow_executable_fixture(self):
        self.fixture_exception()
        self.protected.chmod(0o755)
        self.assertEqual(self.run_gate().returncode, 1)

    def test_exception_does_not_allow_symlink_fixture(self):
        self.fixture_exception()
        target = self.root / 'copy.swift'
        target.write_bytes(self.protected.read_bytes())
        self.protected.unlink()
        self.protected.symlink_to(target)
        self.assertEqual(self.run_gate().returncode, 1)

    def test_exception_does_not_allow_missing_fixture(self):
        self.fixture_exception()
        self.protected.unlink()
        self.assertEqual(self.run_gate().returncode, 1)

    def test_exception_does_not_override_host_import_prohibition(self):
        self.fixture_exception()
        (self.core / 'File.swift').write_text('import AppKit\n')
        self.assertEqual(self.run_gate().returncode, 1)


if __name__ == '__main__':
    unittest.main()
