#!/usr/bin/env python3
"""Exercise actual verifier initialization without package tools or payload execution."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'verify-linux-packages.sh'


class PackageVersionTests(unittest.TestCase):
    def test_versions_remain_literal_package_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            (root / 'linux').mkdir()
            shutil.copyfile(SOURCE, root / 'scripts/verify-linux-packages.sh')
            # The first dependency is sourced after version initialization.
            # Stop here, before artifact reads or runtime verification can execute.
            (root / 'linux/arch.sh').write_text('printf "%s\\n" "$PACKAGE_VERSION"\nexit 0\n')
            cases = {
                '1.2.3': '1.2.3',
                'v1.2.3': '1.2.3',
                '1.2.3-rc.1': '1.2.3~rc.1',
                'v1.2.3-rc-1': '1.2.3~rc-1',
                '1.2.3-rc.1+linux.2': '1.2.3~rc.1+linux.2',
                '1.2.3+linux.2': '1.2.3+linux.2',
            }
            for version, expected in cases.items():
                with self.subTest(version=version):
                    result = subprocess.run(['bash', str(root / 'scripts/verify-linux-packages.sh'), version],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), expected)


if __name__ == '__main__':
    unittest.main()
