#!/usr/bin/env python3
"""The macOS app keeps zmx v0.8.1 while Linux runs v1.

These checks keep each build and cache tied to its backend. Linux's updated client has a
legacy IPC bridge and runs program-start holds through its own patch set.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def assignment(path, key):
    match = re.search(rf'^{key}="([^"]+)"', (ROOT / path).read_text(), re.MULTILINE)
    return match.group(1) if match else None


class ZmxPinSplitTests(unittest.TestCase):
    def test_macos_and_linux_pin_different_zmx_releases(self):
        mac, linux = assignment("scripts/setup.sh", "ZMX_REV"), assignment("linux/zmx.env", "ZMX_REV")
        self.assertRegex(mac or "", r"^[0-9a-f]{40}$")
        self.assertRegex(linux or "", r"^[0-9a-f]{40}$")
        self.assertNotEqual(mac, linux)
        self.assertEqual(assignment("scripts/setup.sh", "ZMX_ZIG_FORMULA"), "zig@0.16")
        self.assertEqual(assignment("linux/zmx.env", "ZMX_ZIG_VERSION"), "0.17.0")

    def test_each_build_reads_its_own_patch_set(self):
        self.assertEqual(assignment("scripts/setup.sh", "ZMX_PATCH_DIR"), "scripts/zmx-patches-macos")
        self.assertIn('"$ROOT/scripts/zmx-patches"/*.patch', (ROOT / "scripts/setup-linux.sh").read_text())
        mac = {p.name for p in (ROOT / "scripts/zmx-patches-macos").glob("*.patch")}
        linux = {p.name for p in (ROOT / "scripts/zmx-patches").glob("*.patch")}
        self.assertIn("0009-legacy-ipc.patch", linux)
        self.assertNotIn("0009-legacy-ipc.patch", mac)
        self.assertIn("0007-drain-output-on-exit.patch", linux)
        self.assertNotIn("0007-drain-output-on-exit.patch", mac)
        self.assertTrue((ROOT / "scripts/zmx-patches-macos/ghostty/0001-formatter-unstyled-blanks.patch").is_file())

    def test_macos_cache_key_follows_the_macos_patch_set(self):
        weekly = (ROOT / ".github/workflows/omarchy-macos.yml").read_text()
        self.assertIn("'scripts/zmx-patches-macos/**/*.patch'", weekly)
        self.assertNotIn("'scripts/zmx-patches/**/*.patch'", weekly)

    def test_host_startup_hold_is_in_the_linux_patch_set(self):
        # Upstream 6ddbc3bd replaced the DA responder with a zmx-side program-start hold.
        host = ROOT / "agtermCore/Sources/SessionHostRuntime/Host.swift"
        self.assertIn("ZMX_HOLD_UNTIL_ATTACH", host.read_text())
        hold = ROOT / "scripts/zmx-patches/0013-hold-until-attach.patch"
        self.assertIn('test "a held child is released by the first terminal Init only', hold.read_text())


if __name__ == "__main__":
    unittest.main()
