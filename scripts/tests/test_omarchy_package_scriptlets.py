import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPTLET = (
    Path(__file__).resolve().parents[2]
    / "packaging/omarchy-pkgs/agterm-omarchy-bin/agterm-omarchy-bin.install"
)


class PackageScriptletTests(unittest.TestCase):
    def test_scriptlets_only_print_claude_activation_instructions(self):
        for action, command in (
            ("post_install", "install"),
            ("post_upgrade", "install"),
            ("pre_remove", "uninstall"),
        ):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as home:
                result = subprocess.run(
                    ["bash", "-c", 'source "$1"; "$2" 0.44.1.0-1', "_", str(SCRIPTLET), action],
                    env={"HOME": home, "PATH": os.defpath},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.count(f"/usr/bin/agtermctl integration {command} hooks --only claude"),
                    1,
                    result.stdout,
                )
                self.assertEqual(list(Path(home).iterdir()), [])
