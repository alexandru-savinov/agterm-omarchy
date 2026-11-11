#!/usr/bin/env python3
"""Add verified native input metadata and observed staged binaries to an SPDX 2.3 document."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re

spec = importlib.util.spec_from_file_location('linux_notices', Path(__file__).with_name('linux-notices.py'))
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


def augment(document, payload):
    require = notices.require
    manifest, manifest_bytes = notices.profile()
    inventory_bytes = notices.read_regular(notices.NOTICES / 'inventory.json')
    inventory = json.loads(inventory_bytes)
    payload = payload.resolve()

    def read(relative):
        path = payload / relative
        require(path.is_relative_to(payload) and '..' not in path.parts,
                'invalid payload path')
        require(not any(parent.is_symlink() for parent in path.parents if parent != payload.parent),
                'linked payload directory')
        return notices.read_regular(path)

    staged = 'share/licenses/agterm/'
    require(read(staged + 'sources.json') == manifest_bytes, 'stale staged notice profile')
    require(read(staged + 'inventory.json') == inventory_bytes, 'stale staged inventory')
    metadata = json.loads(read(staged + 'payload.json'))
    require(metadata['schema'] == 'agterm-linux-notice-payload/1', 'unsupported payload metadata')
    require(metadata['source_profile_sha256'] == notices.digest(manifest_bytes), 'profile digest mismatch')
    libraries = {entry['file']: entry['sha256'] for entry in metadata['bundled_libraries']}
    require(len(libraries) == len(metadata['bundled_libraries']), 'duplicate library metadata')
    require(set(libraries) == {path.name for path in (payload / 'lib').iterdir()},
            'staged library set differs from metadata')
    for name, checksum in libraries.items():
        require(Path(name).name == name, 'invalid library filename')
        require(notices.digest(read('lib/' + name)) == checksum, 'staged library digest mismatch')
        require(name in inventory['bundled_libraries'], 'unmapped staged library')
    zmx_pin = re.search(r'^ZMX_REV="([0-9a-f]{40})"$',
                        (notices.ROOT / 'linux/zmx.env').read_text(), re.MULTILINE)
    zmx_inputs = [entry for entry in inventory['source_inputs'] if entry['repository'] == 'neurosnap/zmx']
    require(zmx_pin and zmx_inputs and all(entry['revision'] == zmx_pin[1] for entry in zmx_inputs),
            'stale zmx source input')
    require(document['spdxVersion'] == 'SPDX-2.3', 'expected SPDX-2.3')
    result = copy.deepcopy(document)
    packages = result.setdefault('packages', [])
    relationships = result.setdefault('relationships', [])
    identifiers = {entry['SPDXID'] for entry in packages + result.get('files', [])}
    identifiers.add(result['SPDXID'])
    require(not any(identifier.startswith('SPDXRef-Native-') for identifier in identifiers),
            'native SPDX identifier collision')

    def add(kind, name, **fields):
        identifier = 'SPDXRef-Native-' + kind + '-' + notices.digest(name.encode())[:24]
        require(identifier not in identifiers, 'duplicate native package')
        identifiers.add(identifier)
        packages.append(dict(SPDXID=identifier, name=name, downloadLocation='NOASSERTION',
                             filesAnalyzed=False, licenseConcluded='NOASSERTION',
                             licenseDeclared='NOASSERTION', copyrightText='NOASSERTION', **fields))
        relationships.append(dict(spdxElementId=result['SPDXID'], relationshipType='DESCRIBES',
                                  relatedSpdxElement=identifier))
        return identifier

    def relate(left, right, comment):
        relationships.append(dict(spdxElementId=left, relationshipType='OTHER',
                                  relatedSpdxElement=right, comment=comment))

    records = {entry['file']: entry for entry in manifest['files']}
    components = {}
    for name, component in sorted(inventory['components'].items()):
        sources = [records[path] for path in component['notices']]
        components[name] = add('Input', name, sourceInfo=json.dumps(sources, sort_keys=True),
                               comment=component['association'])
    zmx = add('Input', 'zmx', versionInfo=zmx_pin[1],
              sourceInfo=json.dumps(zmx_inputs, sort_keys=True),
              comment='Pinned source input; this does not authenticate the staged executable build.')
    for name, archive in sorted(inventory['archives'].items()):
        identifier = add('Archive', name, checksums=[dict(algorithm='SHA256', checksumValue=archive['sha256'])],
                         sourceInfo=archive['url'], comment='Pinned input archive, not an observed shipped binary.')
        for component_name, component in inventory['components'].items():
            if any(records[path].get('archive') == name for path in component['notices']):
                relate(components[component_name], identifier, 'Retained component notice originates in this input archive.')
    mappings = {'lib/' + name: inventory['bundled_libraries'][name] for name in libraries}
    mappings.update(inventory['executables'])
    for relative, names in sorted(mappings.items()):
        require(names and all(name in components for name in names), 'missing mapped component')
        identifier = add('Binary', relative,
                         checksums=[dict(algorithm='SHA256', checksumValue=notices.digest(read(relative)))],
                         comment='Observed staged bytes; source association is notice metadata, not build attestation.')
        for name in names:
            relate(identifier, components[name], 'Pinned notice association; does not prove per-symbol inclusion.')
        if relative == 'bin/zmx':
            relate(identifier, zmx, 'Expected pinned zmx source; executable provenance requires separate build evidence.')
    result['documentNamespace'] += '/native-' + notices.digest(json.dumps(result, sort_keys=True).encode())
    result['creationInfo'].setdefault('creators', []).append('Tool: agterm-native-sbom')
    result['comment'] = (result.get('comment', '') + '\nNative inventory limits: '
                         + '; '.join(inventory['unresolved'])).strip()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sbom', type=Path)
    parser.add_argument('payload', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        result = augment(json.loads(args.sbom.read_text()), args.payload)
        with args.output.open('x') as output:
            output.write(json.dumps(result, indent=2) + '\n')
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Native SBOM: {error}\n')


if __name__ == '__main__':
    main()
