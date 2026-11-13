#!/usr/bin/env python3
"""File-only reproducible appendix comparison. No guest/helper/runtime operations.

Default mode reads all input/package files and immutable Git blobs. --prepare is
limited to this task's new private directory; it never writes the input root.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from appendix_support import (Aliases, Checks, digest, encoded, literal_counts,
                              preserved_observations, privacy, require)

SOURCE = '77cce43eb791ba1a983c19760c7df5259d0e452d'
INPUT_SHA = 'b9aa0fbabbdf4ad57f2fc4146505ac64c4ecfe148703092d7783930180c5c48f'
PREVIOUS_SHA = '34bce9ff0f5c61ecafe95670b139d34566eb31e8008b5e1ea8c65d69480dbffc'
PREVIOUS = 'retry-native-complete-publication'
SCHEMA = 'agterm-native-appendix/1'
SCOPE = ('Additive recovery and separate renderer evidence for source ' + SOURCE +
         '. No new native execution, current-main or integrated-candidate acceptance, '
         'trusted hosted release, hardware rendering, or independent-account approval.')
JSON_NAMES = ('native-log-recovery.json', 'native-attempt3-complete.json',
              'native-default-renderer.json', 'native-software-installer-renderer.json',
              'appendix-bindings.json')
SUPPORT = ('input-bindings.json', 'appendix_support.py', 'verify-native-appendix.py')
WORKERS = ('launch-native-default-renderer.py', 'native-default-renderer-worker.py',
           'prepare-native-renderer-install.py', 'launch-native-installed-renderer.py',
           'native-installed-renderer-worker.py', 'collect-native-test-log-bytes.py',
           'bound-77-native/native-run-template.py', 'bound-77-native/harness.sh',
           'bound-77-native/config-probe.py')
EXPECTED_COUNTS = ((692, 98, 0, 1, 1), (693, 99, 0, 0, 0), (4104, 179, 1, 0, 0))


class AppendixAliases(Aliases):
    def text(self, value):
        value = super().text(value)
        value = re.sub(r'none-launches/(\d+)-(\d+)\.json',
                       lambda m: 'none-launches/' + self.alias('process', m[1]) + '-' +
                       self.alias('process-birth', m[2]) + '.json', value)
        return re.sub(r'\b(pid|ppid)(\s*[=:]\s*)(\d+)\b',
                      lambda m: m[1] + m[2] + self.alias('process', m[3]), value)


def private_check(value, checks, label):
    privacy(value, checks, label)
    text = json.dumps(value, ensure_ascii=False)
    for pattern in (r'none-launches/\d+-\d+\.json', r'\b(?:pid|ppid)\s*[=:]\s*\d+\b'):
        checks.check(not re.search(pattern, text), label + ': local process identifier', 'privacy')


def record(data, name):
    return {'path': name, 'bytes': len(data), 'sha256': digest(data)}


def read_inputs(root, package, checks):
    binding_bytes = (package / 'input-bindings.json').read_bytes()
    checks.check(digest(binding_bytes) == INPUT_SHA, 'input snapshot pin', 'binding')
    bindings = json.loads(binding_bytes)
    checks.check(bindings['source'] == SOURCE and len(bindings['inputs']) == 67,
                 'input snapshot source and count', 'binding')
    files = {}
    for row in bindings['inputs']:
        path = root / row['path']
        checks.check(not path.is_symlink() and path.is_file(), 'regular original: ' + row['path'], 'binding')
        data = path.read_bytes()
        checks.check(len(data) == row['bytes'] and digest(data) == row['sha256'],
                     'immutable original: ' + row['path'], 'binding')
        files[row['path']] = data
    checks.check(digest(files[PREVIOUS + '/package-manifest.json']) == PREVIOUS_SHA,
                 'frozen previous manifest pin', 'binding')
    previous = json.loads(files[PREVIOUS + '/package-manifest.json'])
    checks.check(len(previous['originals']) == 38 and len(previous['deliverables']) == 9,
                 'prior package inventory', 'binding')
    for row in previous['originals'] + previous['deliverables']:
        name = row['path'] if row in previous['originals'] else PREVIOUS + '/' + row['path']
        checks.check(record(files[name], row['path']) == row, 'prior package immutable: ' + name, 'binding')
    return files, bindings


def document(files, name):
    original = json.loads(files[name])
    if isinstance(original, dict) and 'stdout' in original and 'signal_code' in original:
        return {'original_file': name, 'original_sha256': digest(files[name]),
                'transport': {k: v for k, v in original.items() if k != 'stdout'},
                'stdout_utf8_sha256': digest(original['stdout'].encode()),
                'stdout_encoding': 'JSON decoded without dropping fields',
                'observation': json.loads(original['stdout'])}
    return {'original_file': name, 'original_sha256': digest(files[name]),
            'transport': None, 'stdout_encoding': 'not a transport wrapper', 'observation': original}


def command(status, label):
    return next(row for row in status['commands'] if row['label'] == label)


def log_stats(raw):
    text = raw.decode('utf-8', errors='strict')
    return {'bytes': len(raw), 'sha256': digest(raw), 'valid_utf8': True,
            'characters': len(text), 'split_line_count': len(text.splitlines()),
            'crlf_pairs': raw.count(b'\r\n'), 'bare_cr': raw.count(b'\r') - raw.count(b'\r\n'),
            'lf_bytes': raw.count(b'\n'), 'replacement_characters': text.count('\ufffd'),
            'literal_counts': literal_counts(text),
            'framework_summary_lines': [line for line in text.splitlines()
                                        if re.match(r'^[✔✘] Test run ', line)],
            'actual_skip_lines': [line for line in text.splitlines()
                                  if re.match(r'^➜ Test .* skipped:', line)]}


def recover_logs(files, checks):
    manifest = document(files, 'native-log-binary-transfer/manifest-private.json')
    corrected = document(files, 'native-log-binary-transfer/collection-comparison-corrected-private.json')
    superseded = document(files, 'native-log-binary-transfer/collection-comparison-private.json')
    checks.check(manifest['observation']['source'] == SOURCE and manifest['observation']['passed'] is True,
                 'binary transfer source and verdict')
    expected_names = ('frontend-attempt2.log', 'frontend-attempt3.log', 'core.log')
    checks.check(tuple(row['name'] for row in manifest['observation']['files']) == expected_names,
                 'three recovered log names')
    source_docs, stats = [], []
    for i, row in enumerate(manifest['observation']['files']):
        raw = files['native-log-binary-transfer/' + row['name']]
        doc = document(files, row['source_record'])
        status = doc['observation']['report' if i == 0 else 'status']
        cmd = command(status, 'core-tests' if i == 2 else 'swift-test')
        summary = log_stats(raw)
        checks.check(summary['sha256'] == row['sha256'] == row['recorded_guest_log_sha256'] == cmd['log_sha256'],
                     'recovered raw hash matches original recorded hash: ' + row['name'], 'binding')
        checks.check(summary['bytes'] == row['bytes'] == cmd['log_bytes'], 'recovered byte count: ' + row['name'])
        checks.check(doc['original_sha256'] == row['source_record_sha256'], 'original source record: ' + row['name'], 'binding')
        checks.check(status['source_commit'] == SOURCE and cmd['exit_code'] == row['exit_code'], 'source and exit: ' + row['name'])
        checks.check(tuple(summary['literal_counts'].values()) == EXPECTED_COUNTS[i], 'literal recount: ' + row['name'])
        checks.check(summary['literal_counts']['test_pass'] == row['literal_passed_test_lines'] and
                     summary['literal_counts']['test_fail'] == row['literal_failed_test_lines'], 'transfer recount: ' + row['name'])
        checks.compare(row['summary_lines'], summary['framework_summary_lines'], 'framework summary: ' + row['name'])
        stats.append({'name': row['name'], 'original_command': cmd, 'statistics': summary})
        source_docs.append(doc)
    comparisons = []
    old_texts = (source_docs[0]['observation']['complete_log'], source_docs[2]['observation']['logs'][0]['text'])
    for index, old in zip((0, 2), old_texts):
        name = expected_names[index]
        raw = files['native-log-binary-transfer/' + name]
        text = raw.decode('utf-8')
        comparison = {'name': name[:-4], 'collection_field': '/complete_log' if index == 0 else '/logs/0/text',
                      'old_matches_universal_newline_normalization': old == text.replace('\r\n', '\n').replace('\r', '\n'),
                      'old_matches_exact_decoded_bytes': old == text,
                      'old_bytes': len(old.encode()), 'raw_bytes': len(raw), 'raw_sha256': digest(raw),
                      'raw_crlf_count': raw.count(b'\r\n'), 'old_replacement_characters': old.count('\ufffd'),
                      'raw_replacement_characters': text.count('\ufffd')}
        checks.check(comparison['old_matches_universal_newline_normalization'] is True and
                     comparison['old_matches_exact_decoded_bytes'] is False, 'newline explanation: ' + name)
        checks.check(len(raw) - len(old.encode()) == raw.count(b'\r\n'), 'byte-size loss explained by CRLF: ' + name)
        comparisons.append(comparison)
        stats[index]['previous_collection_utf8_sha256'] = digest(old.encode())
    checks.compare(corrected['observation'], comparisons, 'independently recomputed corrected comparison')
    checks.check(stats[2]['statistics']['replacement_characters'] == 1 and
                 'fish' in stats[2]['statistics']['actual_skip_lines'][0], 'raw core U+FFFD and fish skip retained')
    attempt2 = source_docs[0]['observation']['report']
    attempt3 = source_docs[1]['observation']['status']
    checks.compare(attempt2['source_input_sha256'], attempt3['source_input_sha256'], 'same source inputs across retry')
    checks.compare(attempt2['toolchain_sha256'], attempt3['toolchain_sha256'], 'same compiler inputs across retry')
    checks.check(len(attempt3['source_input_sha256']) == 11 and len(attempt3['payload_sha256']) == 769,
                 'eleven source inputs and 769 staged files')
    cmd3 = command(attempt3, 'swift-test')
    checks.check('--skip-build' in cmd3['argv'] and not any(x in cmd3['argv'] for x in ('--filter', '--skip')),
                 'third attempt reused compiled tests without selection filter')
    checks.check('pathTooLong' in source_docs[0]['observation']['complete_log'], 'historical path length failure retained')
    recovery = {'schema': SCHEMA, 'scope': SCOPE, 'binary_transfer_manifest': manifest,
                'corrected_collection_comparison': corrected,
                'superseded_text_mode_comparison': superseded,
                'superseded_comparison_note': 'First helper read text with newline conversion; historical output retained, not a current mismatch verdict.',
                'recomputed_raw_log_observations': stats,
                'historical_previous_package': {'manifest_sha256': PREVIOUS_SHA,
                    'note': 'Previous full-text collection limits remain historically correct. Binary recovery resolves the exact-byte and missing complete attempt3 log gaps without rerunning tests or changing that package.'},
                'selection_note': 'Transfer manifest skip_lines is a broad substring search and includes test names. Only anchored actual_skip_lines is used to count skips.'}
    complete = {'schema': SCHEMA, 'scope': SCOPE, 'source_record': source_docs[1],
                'raw_log_file': 'native-log-binary-transfer/frontend-attempt3.log',
                'raw_log_statistics': stats[1]['statistics'],
                'complete_log': files['native-log-binary-transfer/frontend-attempt3.log'].decode('utf-8'),
                'log_policy': 'Complete raw decoded UTF-8 text including CR/LF, with reversible declared identifier substitutions only; raw log hash binds bytes before substitution.'}
    return recovery, complete, attempt3


def renderer_records(files, attempt3, repository, checks):
    default_doc = document(files, 'native-default-renderer-result-private.json')
    software_doc = document(files, 'native-installed-renderer-result-private.json')
    install_doc = document(files, 'native-renderer-private-install-result.json')
    diagnostics_doc = document(files, 'native-default-renderer-diagnostics-private.json')
    default, software, install = [doc['observation'] for doc in (default_doc, software_doc, install_doc)]
    build_binding = document(files, 'native-full-build-complete-bindings-private.json')['observation']
    checks.check(build_binding['source_commit'] == SOURCE and build_binding['payload_file_count'] == 769,
                 'completed build binding source and file count')
    for observed, worker_name in ((default, 'native-default-renderer-worker.py'),
                                  (software, 'native-installed-renderer-worker.py')):
        settings = observed['settings']
        checks.check(observed['state'] == 'complete' and observed['diagnostic_only'] is True,
                     'renderer experiment complete diagnostic scope')
        checks.check(all(settings[k] == SOURCE for k in ('source_commit', 'app_source_commit', 'library_source_commit')),
                     'renderer app/library/source exact source77 binding')
        checks.check(settings['operator_sha256'] == digest(files['bound-77-native/native-run-template.py']) and
                     settings['worker_sha256'] == digest(files[worker_name]), 'renderer exact operator and worker bindings', 'binding')
        checks.check(settings['build_status_sha256'] == build_binding['status_sha256'], 'renderer original build status binding', 'binding')
        checks.compare(build_binding['payload_hashes'], settings['payload_hashes'], 'renderer four payload bindings')
        for name, sha in settings['payload_hashes'].items():
            checks.check(attempt3['payload_sha256'][name] == sha, 'renderer payload matches complete build: ' + name, 'binding')
        checks.check(list(observed['modes']) == ['none'] and observed['config_repetitions'] == [],
                     'only ordinary mode, no Live/config repetition claim')
        mode = observed['modes']['none']
        cleanup = mode['independent_cleanup']
        checks.check(mode['cleanup']['passed'] is True and cleanup['passed'] is True, 'both renderer cleanup audits passed')
        for name in ('remaining_processes', 'new_app_processes', 'retained_recovery_paths', 'cleanup_actions', 'errors'):
            checks.check(cleanup[name] == [], 'renderer cleanup field clear: ' + name)
        checks.check(cleanup['timed_out'] is False and cleanup['forced_cleanup'] is False, 'no forced renderer cleanup')
        for name in ('payload_unchanged', 'frozen_payload_unchanged', 'installed_payload_unchanged',
                     'shared_runtime_unchanged', 'login_shell_unchanged'):
            checks.check(observed[name] is True, 'renderer preservation: ' + name)
        checks.check(observed['preservation'] == {'passed': True, 'protected_process_count': 2, 'lost': {}},
                     'two protected process identities retained')
        observer = observed['image_observer']
        checks.check(observer['completed'] is True and observer['errors'] == [] and
                     observer['missing_expected_images'] == [] and observer['expected_image_count'] == 1 and
                     len(observer['observed_images']) == 1, 'one complete observed renderer image')
        image = observer['observed_images'][0]
        checks.check(image['mapping_count'] == 7 and image['app_sha256'] == settings['payload_hashes']['bin/agterm-linux.bin'] and
                     image['library_sha256'] == settings['payload_hashes']['lib/libghostty.so'], 'renderer actual mapped app/library')
    for name in ('operator_sha256', 'fixture_sha256', 'probe_source_commit', 'library_ghostty_commit'):
        checks.check(default['settings'][name] == software['settings'][name], 'common renderer input: ' + name, 'binding')
    checks.check(default['passed'] is False and default['modes']['none']['passed'] is False and
                 default['modes']['none']['returncode'] == 1 and default['modes']['none']['result']['completed_checks'] == [],
                 'default renderer failure with zero completed checks')
    checks.check(len(default['launch_renderer_environment']) == 7 and
                 all(value is None for value in default['launch_renderer_environment'].values()),
                 'seven renderer launch overrides absent')
    checks.check(software['passed'] is True and software['modes']['none']['passed'] is True and
                 software['modes']['none']['returncode'] == 0 and len(software['modes']['none']['result']['checks']) == 7,
                 'installed software renderer ordinary seven-check pass')
    checks.check(software['launch_renderer_environment']['LIBGL_ALWAYS_SOFTWARE'] == '0' and
                 all(value is None for key, value in software['launch_renderer_environment'].items() if key != 'LIBGL_ALWAYS_SOFTWARE'),
                 'deliberate software launch input zero; other overrides absent')
    checks.compare({'AGTERM_ORIG_LIBGL_ALWAYS_SOFTWARE': '0', 'LIBGL_ALWAYS_SOFTWARE': '1'},
                   software['image_observer']['observed_images'][0]['renderer_environment'], 'kernel-observed installed launcher override')
    checks.check(install['source_commit'] == SOURCE and install['exit_code'] == 0 and install['passed'] is True,
                 'private installer result passed at exact source')
    checks.check('--software-rendering' in install['argv'] and install['renderer'] == 'software', 'explicit software installer mode')
    checks.check(install['copied_payload_count'] == 771 and install['extra_files'] ==
                 ['share/agterm/INSTALLED_FILES', 'share/agterm/RENDERER'], '769 original payload files plus two installer files')
    checks.check(install['source_payload_unchanged'] is True and install['real_installation_unchanged'] is True and
                 install['original_files_unchanged'] is True, 'private installer source and real installation preservation')
    checks.check(software['settings']['payload'] == install['prefix'] + '/lib/agterm' and
                 install['private_launcher_target'] == software['settings']['payload'] + '/bin/agterm-linux',
                 'private installed launcher target binding')
    diagnostics = diagnostics_doc['observation']
    app_log = diagnostics['logs']['none/app.log']
    selector = r'EGL|GtkGLArea|GL context'
    lines = app_log.splitlines()
    selected = [{'line': i, 'text': line} for i, line in enumerate(lines, 1) if re.search(selector, line)]
    checks.check([row['line'] for row in selected] == [95, 97, 99] and len(lines) == 99,
                 'only three GL diagnostic lines selected from full app log')
    checks.check(any('No EGL configuration available' in row['text'] for row in selected), 'specific EGL failure retained')
    projection = {'original_log_utf8_sha256': digest(app_log.encode()), 'original_log_utf8_bytes': len(app_log.encode()),
                  'original_split_line_count': len(lines), 'selection_regex': selector, 'selected_lines': selected,
                  'omitted_line_count': len(lines) - len(selected),
                  'omission_reason': 'Unfiltered Fontconfig and other system/app output stays in the immutable private original. Only the exact GL failure lines are published.'}
    diagnostics_doc = copy.deepcopy(diagnostics_doc)
    diagnostics_doc['observation']['logs']['none/app.log'] = projection
    checks.check(len(diagnostics_doc['observation']['logs']) == 3, 'other two diagnostic logs retained completely')
    prior = json.loads(files[PREVIOUS + '/native-bindings.json'])
    pins = copy.deepcopy(prior['pinned_git_source_bindings'])
    install_data = subprocess.run(['git', '-C', str(repository), 'cat-file', 'blob', SOURCE + ':scripts/install-linux.sh'],
                                  check=True, capture_output=True, timeout=15).stdout
    install_blob = subprocess.run(['git', '-C', str(repository), 'rev-parse', SOURCE + ':scripts/install-linux.sh'],
                                  check=True, capture_output=True, timeout=15).stdout.decode().strip()
    checks.check(digest(install_data) == install['installer_sha256'], 'actual installer Git source hash', 'binding')
    pins.append({'commit': SOURCE, 'path': 'scripts/install-linux.sh', 'git_blob': install_blob,
                 'sha256': digest(install_data), 'bytes': len(install_data)})
    for row in pins:
        data = subprocess.run(['git', '-C', str(repository), 'cat-file', 'blob', row['commit'] + ':' + row['path']],
                              check=True, capture_output=True, timeout=15).stdout
        blob = subprocess.run(['git', '-C', str(repository), 'rev-parse', row['commit'] + ':' + row['path']],
                              check=True, capture_output=True, timeout=15).stdout.decode().strip()
        checks.check(len(data) == row['bytes'] and digest(data) == row['sha256'] and blob == row['git_blob'],
                     'immutable Git source: ' + row['path'], 'binding')
    checks.check(len(pins) == 14, 'fourteen exact Git source bindings', 'binding')
    default_output = {'schema': SCHEMA, 'scope': SCOPE, 'result': default_doc,
                      'launch': document(files, 'native-default-renderer-launch-private.json'),
                      'intermediate_poll': document(files, 'native-default-renderer-poll-private.json'),
                      'diagnostics_projection': diagnostics_doc,
                      'interpretation': 'Default native Wayland launch with seven renderer overrides absent failed to create an EGL context before ordinary checks completed. Successful transport and cleanup do not make the renderer run pass.'}
    software_output = {'schema': SCHEMA, 'scope': SCOPE, 'private_install': install_doc,
                       'launch': document(files, 'native-installed-renderer-launch-private.json'),
                       'result': software_doc,
                       'interpretation': 'Private installer with --software-rendering, then ordinary native Wayland seven-check pass on the exact source77 payload. Deliberate launcher input zero became kernel-observed software one and preserved original zero. This does not demonstrate hardware rendering or current-main acceptance.'}
    return default_output, software_output, pins


def build_expected(files, bindings, repository, checks):
    recovery, complete, attempt3 = recover_logs(files, checks)
    default, software, pins = renderer_records(files, attempt3, repository, checks)
    evidence = [recovery, complete, default, software]
    aliases = AppendixAliases()
    for value in evidence:
        aliases.seed(value)
    public = []
    for name, value in zip(JSON_NAMES[:4], evidence):
        transformed = aliases.value(value)
        preserved_observations(value, transformed, aliases, checks)
        private_check(transformed, checks, name)
        public.append(transformed)
    declaration = aliases.declaration()
    declaration['logs'] = 'Recovered raw logs are strict UTF-8 with exact byte hashes. Complete third-attempt text preserves CR/LF with declared aliases. Older complete logs remain in the frozen previous package; raw-byte recovery comparisons are additive.'
    declaration['intentional_projection'] = 'Only default none/app.log is projected to the three GL error lines at 95,97,99, with original UTF-8 hash/size and omitted count. All other fields in published source records, including timestamps, verdicts, errors, arrays and counts, are retained under reversible identifier aliases.'
    declaration['process_text'] = 'Process ID and birth values in diagnostic filenames and PID assignments use the same declared alias categories.'
    binding_output = {'schema': SCHEMA, 'scope': SCOPE, 'input_snapshot_sha256': INPUT_SHA,
                      'immutable_inputs': bindings['inputs'], 'frozen_previous_package_manifest_sha256': PREVIOUS_SHA,
                      'worker_operator_sources': [record(files[name], name) for name in WORKERS],
                      'pinned_git_source_bindings': pins, 'sanitization': declaration,
                      'authority': 'Preparation and comparison by same-account agent source_review322; root retains review, integration, runtime and publication.'}
    private_check(binding_output, checks, JSON_NAMES[4])
    public.append(binding_output)
    return dict(zip(JSON_NAMES, public))


README = '''### Addendum: exact native logs and separate renderer experiments

This appendix adds retained evidence for source `77cce43eb791ba1a983c19760c7df5259d0e452d`.
It does not rerun tests or change the earlier native evidence package. Its frozen
manifest remains `34bce9ff0f5c61ecafe95670b139d34566eb31e8008b5e1ea8c65d69480dbffc`.

Root recovered the three original guest log files through binary-safe transfer.
The original command records, recovered byte counts and SHA256 hashes agree:

| Log | Raw bytes | Literal test passes / failures | Raw SHA256 |
| --- | ---: | ---: | --- |
| Frontend attempt 2 | 346178 | 692 / 1 | `3ec99f3eb35a435f5eb51de1fed8648062103c9e509b0700d3a1cb277ad34526` |
| Frontend attempt 3 | 166173 | 693 / 0 | `8ea75f1ea7b0c41dc29065f273f2fee3e74e041f5d50becbff10fc6c5863ca23` |
| Core | 1660011 | 4104 / 0 | `1e9dfc9a3b2a79ca90103c50e2a2b63faf2d2b0955c1bb1d432068219f561866` |

[The complete third-attempt observations](native-attempt3-complete.json) now include
the entire decoded log with declared identifier aliases, retaining original CR/LF.
Its literal recount is 693 passed tests, 99 passed suites, zero failed tests/suites
and zero skips. It reused compiled tests with `--skip-build`, the same 11 source
inputs and the same compiler hashes as attempt 2; it is not a new rebuild result.
The earlier `pathTooLong` failure and first-attempt setup failure remain historical.

[The recovery comparison](native-log-recovery.json) proves the earlier attempt 2
and core text equals the raw UTF-8 text after universal newline normalization.
One and two CRLF pairs respectively explain the byte-size differences; bare CR
characters were also converted to LF without changing byte length. All recovered
logs are valid UTF-8. Core's one U+FFFD was already present in the original bytes.
Core retains the explicit missing-fish skip: the framework reports 4105 tests,
while the anchored literal count is 4104 passes and one skip. The transfer manifest's
broad `skip_lines` selection also contains test names and is not a skip count.
The first text-mode comparison is retained as superseded history. Earlier package
collection-limit statements describe what was available then; this addendum resolves
those collection gaps without editing that package or repeating older complete logs.

[The default-renderer experiment](native-default-renderer.json) failed before any
ordinary checks completed: GTK could not create an EGL context. All seven renderer
launch overrides were absent. The exact three GL diagnostic lines are retained;
unfiltered Fontconfig/system output remains private with an explicit projection
hash, size and omitted-line count. Transport success and successful cleanup do not
turn this failed runtime experiment into a pass.

[The separate software-installer experiment](native-software-installer-renderer.json)
used the exact source77 installer with `--software-rendering` in a private prefix.
It preserved 769 original payload files and added only the two installer metadata
files. Its ordinary native Wayland harness passed all seven checks. Deliberate
launcher input `LIBGL_ALWAYS_SOFTWARE` value `0` became kernel-observed value `1`,
while `AGTERM_ORIG_LIBGL_ALWAYS_SOFTWARE` retained `0`. Both renderer experiments
record successful independent cleanup, two preserved protected process identities,
and unchanged original payload, real installation, shared runtime and login shell.
This demonstrates the private software launcher behavior; hardware rendering,
current main, an integrated candidate and trusted hosted release acceptance remain
outside this evidence.

[Bindings](appendix-bindings.json) cover 67 immutable inputs, nine worker/operator
sources and 14 exact Git blobs. [The verifier](verify-native-appendix.py) only reads
files and immutable Git objects. It recomputes hashes/counts/newline comparisons,
reverses declared aliases to compare retained observations, scans publication files
for private identifiers, and exercises eight negative controls. Results are in
[verification.json](verification.json); exact deliverable hashes are in
[package-manifest.json](package-manifest.json). This is same-account preparation,
not independent approval or a new runtime gate.

For root's local recheck, supply the unchanged private evidence root and a local
repository containing the pinned commits:

```sh
python3 -B verify-native-appendix.py --original-root "$PRIVATE_EVIDENCE_ROOT" --repository "$SOURCE_REPOSITORY"
```

The default command is read-only. Root owns integration into #317 and publication.
'''


def negative_controls(public):
    results = []
    sample = {'checks': ['one', 'two'], 'passed': True, 'count': 7, 'started_at': 1794435208.7329493}
    mutations = []
    value = copy.deepcopy(sample); value['count'] = 8
    mutations.append(('changed count rejected', lambda candidate=value: Checks().compare(sample, candidate, 'negative count')))
    value = copy.deepcopy(sample); del value['passed']
    mutations.append(('removed field rejected', lambda candidate=value: Checks().compare(sample, candidate, 'negative field')))
    value = copy.deepcopy(sample); value['checks'].pop()
    mutations.append(('removed array observation rejected', lambda candidate=value: Checks().compare(sample, candidate, 'negative array')))
    value = copy.deepcopy(sample); value['started_at'] += 0.000001
    mutations.append(('changed timestamp rejected', lambda candidate=value: Checks().compare(sample, candidate, 'negative timestamp')))
    mutations.extend([
        ('personal path rejected', lambda: private_check('/' + 'home' + '/synthetic-person/file', Checks(), 'negative path')),
        ('desktop assignment rejected', lambda: private_check('WAYLAND' + '_DISPLAY' + '=' + 'synthetic', Checks(), 'negative desktop')),
        ('credential token rejected', lambda: private_check('gh' + 'p_' + 'X' * 25, Checks(), 'negative credential')),
        ('changed complete log rejected', lambda: Checks().compare(public['native-attempt3-complete.json']['complete_log'],
                                                                public['native-attempt3-complete.json']['complete_log'] + 'changed', 'negative log')),
    ])
    for name, action in mutations:
        try:
            action()
        except ValueError:
            results.append({'control': name, 'rejected': True})
        else:
            raise ValueError('Negative control unexpectedly accepted: ' + name)
    return results


def run(args):
    package = Path(__file__).resolve().parent
    root, repository = Path(args.original_root).resolve(), Path(args.repository).resolve()
    require(package != root, 'separate package directory')
    if args.prepare:
        require(package.name == 'source-native-appendix-sr305ap1112' and package.parent == root,
                'preparation is restricted to the new private task package')
    checks = Checks()
    files, bindings = read_inputs(root, package, checks)
    public = build_expected(files, bindings, repository, checks)
    primary = {name: encoded(value) for name, value in public.items()}
    primary['README.native-appendix-insertion.md'] = README.encode()
    private_check(README, checks, 'README')
    for name in SUPPORT:
        private_check((package / name).read_text(), checks, name)
    controls = negative_controls(public)
    checks.check(len(controls) == 8 and all(row['rejected'] for row in controls), 'eight negative controls', 'negative_controls')
    if args.prepare:
        for name, data in primary.items():
            (package / name).write_bytes(data)
    for name, expected in primary.items():
        actual = (package / name).read_bytes()
        checks.check(actual == expected, 'exact public file bytes: ' + name, 'comparison')
        if name.endswith('.json'):
            checks.compare(json.loads(expected), json.loads(actual), 'published ' + name)
    # Read originals a second time after all derivation and publication comparisons.
    for row in bindings['inputs']:
        data = (root / row['path']).read_bytes()
        checks.check(len(data) == row['bytes'] and digest(data) == row['sha256'],
                     'final immutable input: ' + row['path'], 'binding')
    report = {'schema': 'agterm-native-appendix-verification/1', 'source': SOURCE,
              'scope': SCOPE, 'passed': True, 'checks': sum(checks.counts.values()),
              'checks_by_category': dict(sorted(checks.counts.items())),
              'immutable_inputs': 67, 'pinned_git_sources': 14,
              'negative_controls': controls,
              'limits': ['Only file-only validation was executed for this preparation.',
                         'Original runtime assertions are retained observations, not rerun here.',
                         'The diagnostic projection intentionally omits 96 of 99 raw app log lines.',
                         'Privacy checks cover declared identifier/credential patterns; root retains publication review.',
                         'Verification-report/manifest equality and exact file inventory are checked after these counts.']}
    primary['verification.json'] = encoded(report)
    deliverables = [record(primary[name], name) for name in sorted(primary)]
    deliverables += [record((package / name).read_bytes(), name) for name in SUPPORT]
    deliverables.sort(key=lambda row: row['path'])
    manifest = {'schema': SCHEMA, 'scope': SCOPE, 'source': SOURCE,
                'frozen_previous_package_manifest_sha256': PREVIOUS_SHA,
                'inputs': bindings['inputs'], 'deliverables': deliverables,
                'self_hash_note': 'This file does not contain its own hash. The public handoff binds its exact digest.'}
    if args.prepare:
        (package / 'verification.json').write_bytes(primary['verification.json'])
        (package / 'package-manifest.json').write_bytes(encoded(manifest))
    require((package / 'verification.json').read_bytes() == primary['verification.json'], 'verification report equality')
    require((package / 'package-manifest.json').read_bytes() == encoded(manifest), 'package manifest equality')
    private_check(report, Checks(), 'verification report')
    private_check(manifest, Checks(), 'manifest')
    require(sorted(path.name for path in package.iterdir()) == sorted([row['path'] for row in deliverables] + ['package-manifest.json']),
            'exact package file inventory')
    print(json.dumps({'verification': report, 'manifest_sha256': digest(encoded(manifest)),
                      'deliverables': deliverables}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--prepare', action='store_true')
    run(parser.parse_args())
