#!/usr/bin/env python3
"""Packaging output safety using temporary sentinels and fake build dependencies."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'package-linux.sh'


class PackageOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        (self.repo / 'linux').mkdir()
        shutil.copyfile(SOURCE, self.repo / 'scripts/package-linux.sh')
        (self.repo / 'linux/arch.sh').write_text('HOST_ARCH=x86_64\nPACKAGE_ARCH=amd64\n')
        stage = self.repo / 'scripts/stage-linux.sh'
        stage.write_text('#!/usr/bin/env bash\nset -eu\n[[ ${FAIL_STAGE:-0} != 1 ]] || exit 9\nmkdir -p "$1"\nprintf payload > "$1/data"\n')
        stage.chmod(0o755)
        nfpm = self.root / 'nfpm'
        nfpm.write_text('#!/usr/bin/env bash\nset -eu\n[[ ${FAIL_PACKAGE:-0} != 1 ]] || exit 8\nwhile (( $# )); do\n if [[ $1 == --target ]]; then printf package > "$2"; exit; fi\n shift\ndone\nexit 7\n')
        nfpm.chmod(0o755)
        self.env = dict(os.environ, NFPM=str(nfpm))
        self.out = self.root / 'output with spaces'

    def run_package(self, output=None, **env):
        args = ['bash', str(self.repo / 'scripts/package-linux.sh'), '1.2.3']
        if output is not None:
            args.append(str(output))
        return subprocess.run(args, env=dict(self.env, **env), capture_output=True, text=True, timeout=15)

    def sentinel(self, directory):
        directory.mkdir()
        p = directory / 'keep.txt'
        p.write_text('user data')
        return p

    def test_nonempty_output_preserved_on_stage_failure(self):
        p = self.sentinel(self.out)
        result = self.run_package(self.out, FAIL_STAGE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.read_text(), 'user data')

    def test_nonempty_output_rejected_even_when_build_would_succeed(self):
        p = self.sentinel(self.out)
        result = self.run_package(self.out)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.read_text(), 'user data')
        self.assertEqual(list(self.out.iterdir()), [p])

    def test_empty_output_preserved_on_stage_failure(self):
        self.out.mkdir()
        inode = self.out.stat().st_ino
        self.assertNotEqual(self.run_package(self.out, FAIL_STAGE='1').returncode, 0)
        self.assertEqual(self.out.stat().st_ino, inode)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_missing_output_not_created_on_packager_failure(self):
        self.assertNotEqual(self.run_package(self.out, FAIL_PACKAGE='1').returncode, 0)
        self.assertFalse(self.out.exists())

    def test_symlink_output_preserves_link_and_target(self):
        target = self.root / 'target'
        p = self.sentinel(target)
        self.out.symlink_to(target, target_is_directory=True)
        self.assertNotEqual(self.run_package(self.out).returncode, 0)
        self.assertTrue(self.out.is_symlink())
        self.assertEqual(p.read_text(), 'user data')

    def test_default_clean_dist_flow(self):
        result = self.run_package()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.repo / 'dist-linux'
        self.assertEqual(len(list(out.iterdir())), 4)
        checked = subprocess.run(['sha256sum', '-c', 'agterm-linux-v1.2.3-SHA256SUMS'], cwd=out, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_existing_empty_output_with_spaces(self):
        self.out.mkdir()
        result = self.run_package(self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.out.iterdir())), 4)


if __name__ == '__main__':
    unittest.main()
