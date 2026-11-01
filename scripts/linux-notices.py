#!/usr/bin/env python3
"""Verify and retain pinned distribution notices; does not certify transitive license coverage."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / 'packaging/linux/notices'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_regular(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
            f'missing, empty or linked notice: {path}')
    return path.read_bytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def profile():
    data = read_regular(NOTICES / 'sources.json')
    manifest = json.loads(data)
    require(manifest['schema'] == 'agterm-linux-notices/1', 'unsupported notice profile')
    pin = re.search(r'^GHOSTTY_REV="([0-9a-f]{40})"$',
                    (ROOT / 'linux/ghostty-resources.env').read_text(), re.MULTILINE)
    require(pin and manifest['ghostty_revision'] == pin[1], 'stale Ghostty notice revision')
    paths = set()
    for entry in manifest['files']:
        name = entry['file']
        require(re.fullmatch(r'[a-z0-9][a-z0-9.-]*/[A-Za-z0-9_.-]+', name) and name not in paths,
                'invalid or duplicate notice destination')
        paths.add(name)
        require(re.fullmatch(r'[0-9a-f]{40}', entry['revision']), 'notice source needs an immutable commit')
        require(entry['url'] == f"https://raw.githubusercontent.com/{entry['repository']}/{entry['revision']}/{entry['source_path']}",
                'notice URL does not match source identity')
        require(digest(read_regular(NOTICES / name)) == entry['sha256'], f'notice digest mismatch: {name}')
    return manifest, data


def ghostty_record(manifest):
    matches = [entry for entry in manifest['files'] if entry['file'] == 'ghostty/LICENSE']
    require(len(matches) == 1 and matches[0]['revision'] == manifest['ghostty_revision'],
            'missing pinned Ghostty notice')
    return matches[0]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.chmod(0o644)


def verify_ghostty(directory, manifest):
    expected = ghostty_record(manifest)
    require(read_regular(directory / 'LICENSE') == read_regular(NOTICES / 'ghostty/LICENSE'),
            'stale or changed Ghostty cache LICENSE')
    require(json.loads(read_regular(directory / 'provenance.json')) == expected,
            'stale Ghostty cache notice provenance')


def cache_ghostty(directory, source, manifest):
    expected = ghostty_record(manifest)
    require(digest(read_regular(source / 'LICENSE')) == expected['sha256'],
            'checked-out Ghostty LICENSE differs from pinned notice')
    require(not directory.exists(), 'Ghostty notice destination already exists')
    directory.mkdir(parents=True)
    shutil.copyfile(source / 'LICENSE', directory / 'LICENSE')
    (directory / 'LICENSE').chmod(0o644)
    write_json(directory / 'provenance.json', expected)
    verify_ghostty(directory, manifest)


def stage(destination, manifest, manifest_bytes):
    # Validate every input before creating the notice tree, including restored vendor metadata.
    vendor = ROOT / 'agterm-linux/vendor/ghostty/share/licenses/ghostty'
    verify_ghostty(vendor, manifest)
    license_bytes = read_regular(ROOT / 'LICENSE')
    zmx = read_regular(ROOT / 'agterm-linux/vendor/zmx/LICENSE')
    guide = read_regular(NOTICES / 'README.md')
    target = destination / 'share/licenses/agterm'
    require(not target.exists(), 'staged notice destination already exists')
    libraries = []
    for library in sorted((destination / 'lib').iterdir()):
        require(library.is_file() and not library.is_symlink(), 'unexpected staged library entry')
        libraries.append(dict(file=library.name, sha256=digest(library.read_bytes())))
    target.mkdir(parents=True)
    for entry in manifest['files']:
        output = target / entry['file']
        output.parent.mkdir(exist_ok=True)
        shutil.copyfile(NOTICES / entry['file'], output)
        output.chmod(0o644)
    for name, content in [('LICENSE', license_bytes), ('ZMX-LICENSE', zmx), ('README.md', guide),
                          ('sources.json', manifest_bytes)]:
        (target / name).write_bytes(content)
        (target / name).chmod(0o644)
    write_json(target / 'payload.json', dict(schema='agterm-linux-notice-payload/1',
               source_profile_sha256=digest(manifest_bytes), agterm_license_sha256=digest(license_bytes),
               zmx_license_sha256=digest(zmx), bundled_libraries=libraries,
               coverage='Pinned component notices; not a complete transitive dependency or toolchain attestation.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('verify-ghostty', 'stage'):
        commands.add_parser(name).add_argument('directory', type=Path)
    cache = commands.add_parser('cache-ghostty')
    cache.add_argument('directory', type=Path)
    cache.add_argument('source', type=Path)
    commands.add_parser('verify-profile')
    args = parser.parse_args()
    try:
        manifest, data = profile()
        if args.command == 'cache-ghostty':
            cache_ghostty(args.directory, args.source, manifest)
        elif args.command == 'verify-ghostty':
            verify_ghostty(args.directory, manifest)
        elif args.command == 'stage':
            stage(args.directory, manifest, data)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Linux notices: {error}\n')


if __name__ == '__main__':
    main()
