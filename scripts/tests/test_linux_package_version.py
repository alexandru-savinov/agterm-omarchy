#!/usr/bin/env python3
"""Release version grammar, package version mapping and release-time checks, without package tools."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / 'scripts'
SOURCE = SCRIPTS / 'verify-linux-packages.sh'
WORKFLOW = REPO / '.github/workflows/release-linux.yml'

# Versions both nFPM 2.47.0 packagers and verify-linux-packages.sh map to the same metadata.
ACCEPTED = [
    '0.0.0',
    '1.2.3',
    '10.20.30',
    '1.2.3-rc.1',
    '1.2.3-rc1',
    '1.2.3-0',
    '1.2.3-0a',
    '1.2.3-alpha.beta.10',
    '1.2.3+linux.2',
    '1.2.3-rc.1+linux.2',
]
# nFPM 2.47.0 rewrites '-' inside a prerelease to '_' for RPM only (rpm/rpm.go formatVersion), and
# keeps the whole string as the version when Masterminds/semver rejects it (nfpm.go parseSemver):
# leading zeros in any numeric identifier. The verifier expects neither, so both fail at verification.
REJECTED = [
    '1.2.3-rc-1',
    '1.2.3-rc.01',
    '1.2.3-01',
    '01.2.3',
    '1.02.3',
    '1.2.03',
    '1.2.3-',
    '1.2.3-rc..1',
    '1.2.3-rc.',
    '1.2.3-rc_1',
    '1.2.3+linux.0',
    '1.2.3+build.1',
    '1.2',
    '1.2.3.4',
]


def nfpm_version(version, packager):
    """Model nFPM 2.47.0 semver handling for the accepted grammar (deb/deb.go controlTemplate,
    rpm/rpm.go formatVersion); release is the nfpm.yml value 1."""
    core, _, metadata = version.partition('+')
    base, _, prerelease = core.partition('-')
    result = base
    if prerelease:
        result += '~' + (prerelease.replace('-', '_') if packager == 'rpm' else prerelease)
    if metadata:
        result += '+' + metadata
    return result + '-1'


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=30, **kwargs)


def stub_root(temp, script):
    """Copy SCRIPT beside a linux/arch.sh stub that reports PACKAGE_VERSION and stops the script."""
    root = Path(temp)
    (root / 'scripts').mkdir()
    (root / 'linux').mkdir()
    shutil.copyfile(SCRIPTS / script, root / 'scripts' / script)
    (root / 'linux/arch.sh').write_text('printf "%s\\n" "${PACKAGE_VERSION:-reached}"\nexit 0\n')
    return root / 'scripts' / script


class PackageVersionTests(unittest.TestCase):
    def test_versions_remain_literal_package_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            script = stub_root(temp, 'verify-linux-packages.sh')
            cases = {
                '1.2.3': '1.2.3',
                'v1.2.3': '1.2.3',
                '1.2.3-rc.1': '1.2.3~rc.1',
                '1.2.3-rc.1+linux.2': '1.2.3~rc.1+linux.2',
                '1.2.3+linux.2': '1.2.3+linux.2',
            }
            for version, expected in cases.items():
                with self.subTest(version=version):
                    result = run(['bash', str(script), version])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), expected)

    def test_verifier_expectation_matches_both_nfpm_packagers(self):
        with tempfile.TemporaryDirectory() as temp:
            script = stub_root(temp, 'verify-linux-packages.sh')
            for version in ACCEPTED:
                with self.subTest(version=version):
                    result = run(['bash', str(script), version])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = result.stdout.strip() + '-1'
                    self.assertEqual(expected, nfpm_version(version, 'deb'))
                    self.assertEqual(expected, nfpm_version(version, 'rpm'))

    def test_package_and_verify_scripts_share_the_grammar(self):
        for name in ('package-linux.sh', 'verify-linux-packages.sh'):
            with tempfile.TemporaryDirectory() as temp:
                script = stub_root(temp, name)
                out = Path(temp) / 'absent-output'
                for version in ACCEPTED:
                    with self.subTest(script=name, version=version):
                        result = run(['bash', str(script), version, str(out)])
                        self.assertEqual(result.returncode, 0, result.stderr)
                for version in REJECTED:
                    with self.subTest(script=name, version=version):
                        result = run(['bash', str(script), version, str(out)])
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn('invalid package version', result.stderr)
                        self.assertIn('without leading zeros', result.stderr)
                        self.assertEqual(result.stdout, '')

    def test_tag_validators_share_the_grammar(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = str(Path(temp) / 'missing')
            env = {k: v for k, v in os.environ.items() if k != 'GITHUB_REPOSITORY'}
            # Each valid tag is stopped by a deterministic later check (exit 1), with no network access.
            commands = {
                'verify-published-linux-release.sh': lambda tag: run(
                    ['bash', str(SCRIPTS / 'verify-published-linux-release.sh'), tag],
                    env={**env, 'AGTERM_RELEASE_DOWNLOAD_DIR': missing}),
                'write-linux-release-notes.sh': lambda tag: run(
                    ['bash', str(SCRIPTS / 'write-linux-release-notes.sh'), tag, '0' * 40,
                     str(Path(temp) / 'notes.md'), missing], env=env),
            }
            for name, command in commands.items():
                for version in ACCEPTED:
                    with self.subTest(script=name, version=version):
                        result = command('linux-v' + version)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertNotIn('invalid', result.stderr)
                for version in REJECTED:
                    with self.subTest(script=name, version=version):
                        result = command('linux-v' + version)
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn('invalid Linux release tag', result.stderr)

    def test_release_workflow_rejects_tags_before_building(self):
        text = WORKFLOW.read_text()
        match = re.search(r'^\s*(if \[\[ ! "\$TAG" =~ .*? \]\]; then)$', text, re.M)
        self.assertIsNotNone(match)
        snippet = match.group(1) + ' exit 3; fi'
        env = {'PATH': os.environ['PATH']}
        for version in ACCEPTED:
            with self.subTest(version=version):
                result = run(['bash', '-c', snippet], env={**env, 'TAG': 'linux-v' + version})
                self.assertEqual(result.returncode, 0, result.stderr)
        for version in REJECTED:
            with self.subTest(version=version):
                result = run(['bash', '-c', snippet], env={**env, 'TAG': 'linux-v' + version})
                self.assertEqual(result.returncode, 3, result.stderr)

    def test_release_workflow_publishes_prereleases_as_prereleases(self):
        text = WORKFLOW.read_text()
        self.assertIn('echo "prerelease=$PRERELEASE"', text)
        self.assertIn('prerelease: ${{ steps.release.outputs.prerelease }}', text)
        self.assertIn("make_latest: ${{ steps.release.outputs.prerelease == 'true' && 'false' || 'true' }}", text)
        self.assertNotRegex(text, r'make_latest:\s*true\s*$')
        self.assertIn('run: env -u GITHUB_REPOSITORY scripts/test-published-linux-release.sh', text)


class ReleaseNotesTests(unittest.TestCase):
    def notes(self, tag, temp):
        bin_dir = Path(temp) / 'bin'
        bin_dir.mkdir(exist_ok=True)
        marker = Path(temp) / 'curl-called'
        (bin_dir / 'curl').write_text(f'#!/bin/sh\ntouch {marker}\nexit 22\n')
        (bin_dir / 'curl').chmod(0o755)
        output = Path(temp) / 'notes.md'
        args = ['bash', str(SCRIPTS / 'write-linux-release-notes.sh'), tag, 'f' * 40, str(output)]
        env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'GITHUB_REPOSITORY': 'example/agterm-fork'}
        result = run(args, cwd=REPO, env=env)
        return result, output, marker

    def test_stable_notes_bind_verification_to_release_workflow_and_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            result, output, marker = self.notes('linux-v0.14.0', temp)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text()
            self.assertIn('## Upstream v0.14.0', text)
            self.assertNotIn('This is a prerelease', text)
            self.assertIn('gh attestation verify agterm-linux-v0.14.0-x86_64.tar.gz --repo example/agterm-fork \\\n'
                          '  --signer-workflow example/agterm-fork/.github/workflows/release-linux.yml '
                          '--source-ref refs/tags/linux-v0.14.0\n', text)
            self.assertFalse(marker.exists())

    def test_prerelease_notes_never_query_an_upstream_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            result, output, marker = self.notes('linux-v0.99.0-rc.1', temp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), 'prerelease notes queried the upstream release API')
            text = output.read_text()
            self.assertNotIn('## Upstream', text)
            self.assertIn('This is a prerelease, not a stable release.', text)
            self.assertIn('--source-ref refs/tags/linux-v0.99.0-rc.1\n', text)

    def test_stable_notes_still_require_upstream_product_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            result, _, marker = self.notes('linux-v0.99.0', temp)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.exists())


class PackageVerificationOrderTests(unittest.TestCase):
    def test_checksums_are_checked_before_any_artifact_is_opened(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            (root / 'linux').mkdir()
            shutil.copyfile(SOURCE, root / 'scripts/verify-linux-packages.sh')
            (root / 'linux/arch.sh').write_text('HOST_ARCH=x86_64\nPACKAGE_ARCH=amd64\n')
            out = root / 'dist'
            out.mkdir()
            names = [f'agterm-linux-v1.2.3-x86_64.{ext}' for ext in ('tar.gz', 'deb', 'rpm')]
            for name in names:
                (out / name).write_text(f'fixture {name}\n')
            sums = run(['sha256sum', *names], cwd=out)
            self.assertEqual(sums.returncode, 0, sums.stderr)
            (out / 'agterm-linux-v1.2.3-SHA256SUMS').write_text(sums.stdout)
            (out / names[0]).write_text('tampered\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            marker = root / 'opened'
            for tool in ('tar', 'dpkg-deb', 'rpm', 'rpm2cpio', 'cpio', 'desktop-file-validate', 'file', 'ldd'):
                (bin_dir / tool).write_text(f'#!/bin/sh\necho {tool} >> {marker}\nexit 0\n')
                (bin_dir / tool).chmod(0o755)
            env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'}
            result = run(['bash', str(root / 'scripts/verify-linux-packages.sh'), '1.2.3', str(out)], env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('FAILED', result.stdout + result.stderr)
            self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')


class PublishedReleaseFixtureTests(unittest.TestCase):
    def test_fixture_passes_in_the_release_environment(self):
        # GitHub Actions always sets GITHUB_REPOSITORY, including in release-linux.yml.
        for repository in ('alexandru-savinov/agterm-omarchy', None):
            with self.subTest(repository=repository):
                env = {k: v for k, v in os.environ.items() if k != 'GITHUB_REPOSITORY'}
                if repository:
                    env['GITHUB_REPOSITORY'] = repository
                result = run(['bash', str(SCRIPTS / 'test-published-linux-release.sh')], env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
