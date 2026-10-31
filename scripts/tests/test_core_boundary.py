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


if __name__ == '__main__':
    unittest.main()
