#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('native_sbom', ROOT / 'scripts/native-sbom.py')
sbom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbom)


class NativeSBOMTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.payload = Path(self.temporary.name)
        self.inventory = json.loads((sbom.notices.NOTICES / 'inventory.json').read_text())
        self.manifest, self.manifest_bytes = sbom.notices.profile()
        staged = self.payload / 'share/licenses/agterm'
        staged.mkdir(parents=True)
        (staged / 'sources.json').write_bytes(self.manifest_bytes)
        (staged / 'inventory.json').write_bytes((sbom.notices.NOTICES / 'inventory.json').read_bytes())
        (self.payload / 'lib').mkdir()
        (self.payload / 'lib/libghostty.so').write_bytes(b'disposable library fixture')
        for relative in self.inventory['executables']:
            target = self.payload / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b'disposable executable fixture')
        self.metadata_path = staged / 'payload.json'
        self.metadata_path.write_text(json.dumps(dict(
            schema='agterm-linux-notice-payload/1',
            source_profile_sha256=sbom.notices.digest(self.manifest_bytes),
            bundled_libraries=[dict(file='libghostty.so', sha256=sbom.notices.digest(b'disposable library fixture'))])))
        self.document = dict(spdxVersion='SPDX-2.3', SPDXID='SPDXRef-DOCUMENT', dataLicense='CC0-1.0',
                             name='fixture', documentNamespace='https://example.test/fixture',
                             creationInfo=dict(creators=['Tool: fixture'], created='2026-11-11T00:00:00Z'),
                             packages=[dict(SPDXID='SPDXRef-swift', name='swift-existing',
                                            downloadLocation='NOASSERTION', filesAnalyzed=False)], relationships=[])

    def test_observed_bytes_and_pinned_inputs_preserve_original_and_limits(self):
        original = copy.deepcopy(self.document)
        result = sbom.augment(self.document, self.payload)
        self.assertEqual(self.document, original)
        self.assertEqual(result['packages'][0], original['packages'][0])
        packages = {entry['name']: entry for entry in result['packages']}
        self.assertEqual(packages['zmx']['versionInfo'], '8bab1f0173b07e79835ea372d749af3dbf0d0842')
        self.assertEqual(packages['lib/libghostty.so']['checksums'][0]['checksumValue'],
                         sbom.notices.digest(b'disposable library fixture'))
        self.assertIn('not build attestation', packages['lib/libghostty.so']['comment'])
        self.assertIn(self.inventory['unresolved'][0], result['comment'])
        identifiers = {entry['SPDXID'] for entry in result['packages']} | {result['SPDXID']}
        self.assertTrue(all(item['spdxElementId'] in identifiers and item['relatedSpdxElement'] in identifiers
                            for item in result['relationships']))
        self.assertEqual(result, sbom.augment(self.document, self.payload))

    def test_tampered_library_and_missing_executable_fail(self):
        library = self.payload / 'lib/libghostty.so'
        library.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            sbom.augment(self.document, self.payload)
        library.write_bytes(b'disposable library fixture')
        (self.payload / 'bin/zmx').unlink()
        with self.assertRaisesRegex(ValueError, 'missing, empty or linked'):
            sbom.augment(self.document, self.payload)

    def test_unlisted_library_and_symlink_fail(self):
        extra = self.payload / 'lib/extra.so'
        extra.write_bytes(b'unlisted')
        with self.assertRaisesRegex(ValueError, 'library set'):
            sbom.augment(self.document, self.payload)
        extra.unlink()
        target = self.payload / 'bin/zmx'
        target.unlink()
        target.symlink_to(self.payload / 'bin/agtermctl.bin')
        with self.assertRaisesRegex(ValueError, 'linked'):
            sbom.augment(self.document, self.payload)

    def test_stale_profile_and_spdx_collision_fail(self):
        self.document['packages'][0]['SPDXID'] = 'SPDXRef-Native-existing'
        with self.assertRaisesRegex(ValueError, 'collision'):
            sbom.augment(self.document, self.payload)
        self.document['packages'][0]['SPDXID'] = 'SPDXRef-swift'
        (self.payload / 'share/licenses/agterm/sources.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'stale staged notice profile'):
            sbom.augment(self.document, self.payload)

    def test_missing_component_and_stale_zmx_fail(self):
        inventory = copy.deepcopy(self.inventory)
        inventory['components'].pop('ghostty')
        with self.assertRaisesRegex(ValueError, 'runtime notice association'):
            sbom.notices.verify_inventory(inventory, {entry['file'] for entry in self.manifest['files']})
        real_read = Path.read_text
        def changed_pin(path, *args, **kwargs):
            if path == sbom.notices.ROOT / 'linux/zmx.env':
                return 'ZMX_REV="' + '0' * 40 + '"\n'
            return real_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', changed_pin), self.assertRaisesRegex(ValueError, 'stale zmx'):
            sbom.augment(self.document, self.payload)


if __name__ == '__main__':
    unittest.main()
