#!/usr/bin/env python3
"""The macOS app keeps zmx v0.8.1 while Linux runs v1.

A v1 client cannot reach a v0.8.1 daemon, and only the Linux launch resets those
(LinuxIncompatibleDaemons), so a Mac build on v1 would leave every Live pane of an upgraded user dead.
These checks fail when the two pins converge, or a cache key follows the wrong patch set, before the
Mac launch path can handle the upgrade.
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
        self.assertIn("0007-incompatible-daemon.patch", linux)
        self.assertNotIn("0007-incompatible-daemon.patch", mac)
        self.assertIn("0008-daemon-drain-on-exit.patch", linux)
        self.assertNotIn("0008-daemon-drain-on-exit.patch", mac)
        self.assertTrue((ROOT / "scripts/zmx-patches-macos/ghostty/0001-formatter-unstyled-blanks.patch").is_file())

    def test_macos_cache_key_follows_the_macos_patch_set(self):
        weekly = (ROOT / ".github/workflows/omarchy-macos.yml").read_text()
        self.assertIn("'scripts/zmx-patches-macos/**/*.patch'", weekly)
        self.assertNotIn("'scripts/zmx-patches/**/*.patch'", weekly)

    def test_device_attribute_replies_name_the_macos_pin(self):
        responder = ROOT / "agtermCore/Sources/SessionHostRuntime/DeviceAttributeResponder.swift"
        self.assertIn(f"ZMX_REV {assignment('scripts/setup.sh', 'ZMX_REV')}", responder.read_text())


if __name__ == "__main__":
    unittest.main()
