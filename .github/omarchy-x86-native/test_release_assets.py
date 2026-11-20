#!/usr/bin/env python3
"""release-assets.py against a fake GitHub release; no network."""

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("release_assets", Path(__file__).resolve().parent / "release-assets.py")
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)
SHA = "c" * 40
TAG = f"omarchy-dev-{SHA[:12]}-run9"


class FakeRelease:
    def __init__(self, tag=TAG, version=None):
        names = [f"agterm-omarchy-{version}-{arch}.tar.gz" if version else f"agterm-omarchy-{arch}.tar.gz"
                 for arch in assets.ARCHITECTURES]
        self.files = {name: f"archive {name}".encode() for name in names}
        self.files["promotion.json"] = json.dumps({"source_commit": SHA}).encode()
        self.files["SHA256SUMS"] = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                                           for name, data in self.files.items()).encode()
        self.release = {"tag_name": tag, "draft": False, "assets": [
            {"id": 500 + index, "name": name, "state": "uploaded", "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
            for index, (name, data) in enumerate(self.files.items())]}

    def check_output(self, command):
        path = command[-1].split(f"repos/{assets.REPOSITORY}/", 1)[1]
        if path.startswith("releases/tags/"):
            return json.dumps(self.release).encode()
        asset_id = int(path.rsplit("/", 1)[1])
        name = next(item["name"] for item in self.release["assets"] if item["id"] == asset_id)
        return self.files[name]

    def resolve(self, tag=TAG, arches=assets.ARCHITECTURES):
        with patch.object(assets.subprocess, "check_output", side_effect=self.check_output):
            return assets.resolve(tag, list(arches))


class ReleaseAssetTests(unittest.TestCase):
    def test_resolves_ids_digests_and_the_promoted_commit(self):
        for version in (None, "0.43.0.1"):
            with self.subTest(version=version):
                tag = f"omarchy-v{version}" if version else TAG
                fake = FakeRelease(tag, version)
                resolved = fake.resolve(tag)
                self.assertEqual(resolved["sha"], SHA)
                name = f"agterm-omarchy-{version}-x86_64.tar.gz" if version else "agterm-omarchy-x86_64.tar.gz"
                archive = resolved["archives"]["x86_64"]
                self.assertEqual(archive["name"], name)
                self.assertEqual(archive["sha256"], hashlib.sha256(fake.files[name]).hexdigest())

    def test_rejects_drafts_missing_assets_and_unlisted_digests(self):
        cases = {
            "draft": (lambda fake: fake.release.update(draft=True), "not a published release"),
            "missing archive": (lambda fake: fake.release["assets"].pop(0), "Missing unique asset"),
            "no digest": (lambda fake: fake.release["assets"][1].pop("digest"), "no recorded sha256"),
            "replaced asset": (lambda fake: fake.release["assets"][0].update(digest="sha256:" + "0" * 64),
                               "does not list"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                fake = FakeRelease()
                change(fake)
                with self.assertRaisesRegex(ValueError, message):
                    fake.resolve()
        with self.assertRaisesRegex(ValueError, "Unexpected tag"):
            FakeRelease().resolve(tag="v1.0")

    def test_download_rejects_bytes_that_changed_after_resolution(self):
        fake = FakeRelease()
        archive = fake.resolve()["archives"]["aarch64"]
        fake.files[archive["name"]] = b"replaced"
        with patch.object(assets.subprocess, "check_output", side_effect=fake.check_output), \
                self.assertRaisesRegex(ValueError, "does not match its resolved digest"):
            assets.fetch(archive["id"], archive["sha256"])


if __name__ == "__main__":
    unittest.main()
