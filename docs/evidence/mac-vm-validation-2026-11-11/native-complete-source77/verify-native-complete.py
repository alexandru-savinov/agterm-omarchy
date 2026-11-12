#!/usr/bin/env python3
"""Compare retained native evidence with immutable inputs; never run any worker.

Uses only Python's standard library and bounded, read-only `git cat-file` calls.
No compiler, build, runtime, VM, network, credential, or environment inspection.
"""
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

SOURCE = '77cce43eb791ba1a983c19760c7df5259d0e452d'
PROBE_SOURCE = 'e77b6ad448677e00cafd14a36c557b96affb6678'
REVIEW_URL = 'https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6807859789'
RUNTIME_URL = 'https://github.com/alexandru-savinov/agterm-omarchy/pull/322#issuecomment-6808145530'
PREFIXES = ('native-swift-', 'native-full-build-', 'native-core-test-', 'native-current-source-runtime-')
GROUPS = dict(zip(PREFIXES, ('native-toolchain.json', 'native-build.json', 'native-core.json', 'native-runtime.json')))
# Frozen input snapshot; later unrelated collection files do not extend this audit.
INPUT_FILES = (
    'bound-77-native/binding.json',
    'bound-77-native/config-probe.py',
    'bound-77-native/harness.sh',
    'bound-77-native/native-prepare-template.py',
    'bound-77-native/native-run-template.py',
    'bound-77-native/test-config-probe-operator.py',
    'bound-77-native/test-config-probe-operator.py.log',
    'bound-77-native/test-current-endpoints.py',
    'bound-77-native/test-current-endpoints.py.log',
    'bound-77-native/test-native-helpers.py',
    'bound-77-native/test-native-helpers.py.log',
    'native-core-test-launch-private.json',
    'native-core-test-poll-private.json',
    'native-core-test-result-private.json',
    'native-core-test-worker.py',
    'native-current-source-runtime-launch-private.json',
    'native-current-source-runtime-result-private.json',
    'native-current-source-runtime-summary-private.json',
    'native-current-source-runtime-worker.py',
    'native-full-build-attempt1-private.json',
    'native-full-build-attempt2-test-private.json',
    'native-full-build-complete-bindings-private.json',
    'native-full-build-continuation-launch-private.json',
    'native-full-build-continuation-poll-private.json',
    'native-full-build-continuation3-launch-private.json',
    'native-full-build-continuation3-poll-private.json',
    'native-full-build-launch-private.json',
    'native-full-build-poll-private.json',
    'native-full-build-worker-continuation.py',
    'native-full-build-worker-continuation3.py',
    'native-full-build-worker.py',
    'native-swift-compat-host.py',
    'native-swift-compat-result-private.json',
    'native-swift-prerequisites-private.json',
    'native-swift-toolchain-launch-private.json',
    'native-swift-toolchain-linker-private.json',
    'native-swift-toolchain-poll-private.json',
    'native-swift-toolchain-worker.py',
)
PID_KEYS = {'pid', 'parent', 'supervisor_pid', 'build_pid', 'shell-pid-before', 'shell-pid-after'}
SCHEMA = 'agterm-native-complete-publication/1'
SCOPE = ('Retained complete source77 native branch evidence. No new native execution, '
         'current-main/integrated-candidate acceptance, trusted hosted release, hardware rendering, '
         'bash Live support, or independent account approval is claimed.')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Checks:
    def __init__(self):
        self.counts = collections.Counter()

    def check(self, condition, label, category='evidence'):
        require(condition, label)
        self.counts[category] += 1

    def compare(self, expected, actual, label):
        self.check(type(expected) is type(actual), 'type changed: ' + label, 'comparison')
        if isinstance(expected, dict):
            self.check(list(expected) == list(actual), 'fields/order changed: ' + label, 'comparison')
            for key in expected:
                self.compare(expected[key], actual[key], label + '/' + key)
        elif isinstance(expected, list):
            self.check(len(expected) == len(actual), 'array length changed: ' + label, 'comparison')
            for i, item in enumerate(expected):
                self.compare(item, actual[i], label + '/' + str(i))
        else:
            self.check(expected == actual, 'observation changed: ' + label, 'comparison')


class Aliases:
    """Stable aliases across all files; original values/maps stay private."""
    def __init__(self):
        self.tables = collections.defaultdict(dict)
        self.runs = {}
        self.replacements = collections.Counter()

    def alias(self, category, value):
        table = self.tables[category]
        key = str(value)
        if key not in table:
            table[key] = '<' + category + '-' + f'{len(table) + 1:03d}' + '>'
        self.replacements[category] += 1
        return table[key]

    def seed(self, value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'run_id' and isinstance(child, str):
                    self.runs[child] = self.alias('run', child)
                self.seed(child)
        elif isinstance(value, list):
            for child in value:
                self.seed(child)

    def text(self, value):
        for original, alias in self.runs.items():
            value = value.replace(original, alias)
        rules = (
            ('private-stage', r'/home/[^/\s\"\x27]+/src/agterm-logs/[^/\s\"\x27\)\]:,}]+'),
            ('guest-home', r'/home/[^/\s\"\x27\)\]:,}]+'),
            ('host-home', r'/Users/[^/\s\"\x27\)\]:,}]+'),
            ('session-runtime', r'/run/user/\d+'),
            ('short-temporary-root', r'/var/tmp/(?:nb|nc)\.[A-Za-z0-9_-]+'),
            ('private-runtime', r'/tmp/agt-wl-[A-Za-z0-9_-]+'),
            ('private-zmx-endpoint', r'(?:/tmp/)?agterm-zmx-[A-Za-z0-9_-]+'),
            ('generated-config', r'agterm-ghostty-[A-Za-z0-9]{6}(?![A-Za-z0-9-])'),
            ('sentinel', r'omarchy-harness-sentinel-[0-9a-f]+'),
            ('local-uuid', r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b'),
        )
        for category, pattern in rules:
            value = re.sub(pattern, lambda m: self.alias(category, m[0]), value)
        value = re.sub(r'\((0x[0-9a-fA-F]{8,})\)',
                       lambda m: '(' + self.alias('load-address', m[1]) + ')', value)
        value = re.sub(r'\(([A-Za-z0-9_.-]+):(\d+)\)',
                       lambda m: '(' + m[1] + ':' + self.alias('process', m[2]) + ')', value)
        return value

    def value(self, value, key=''):
        if isinstance(value, dict):
            return {self.text(k): self.value(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v, key) for v in value]
        if key in PID_KEYS and value is not None:
            return self.alias('process', value)
        if key in {'start', 'process_start_ticks'} and value is not None:
            return self.alias('process-birth', value)
        if key == 'uid' and value is not None:
            return self.alias('process-uid', value)
        return self.text(value) if isinstance(value, str) else value

    def declaration(self):
        return {
            'policy': 'Only declared personal/local identifiers are aliased. All fields, array lengths, '
                      'verdicts, timestamps, durations, counts, errors, recovery actions, hashes and public '
                      'source links remain. Process ID/birth/UID values become stable string aliases; '
                      'all other non-string observation values retain their original type and value.',
            'scope': 'One alias table per category shared across the entire package. No reverse maps are published.',
            'categories': {k: len(v) for k, v in sorted(self.tables.items())},
            'stdout_handling': 'JSON-valued transport stdout is decoded to an object; all transport metadata '
                               'is retained separately. Original file/encoded-stdout hashes bind its serialization.',
            'logs': 'Retained collected text is preserved with declared identifier substitutions. Guest-file '
                    'hashes are reported observations, not hashes of normalized or sanitized text. '
                    'Collected-text hashes and byte/line counts are recorded separately.',
            'source_scripts': 'Worker/operator/test source files are bound by original SHA256 and size. '
                              'They are read for identity and selected static assertions, never imported or executed.',
        }


def preserved_observations(before, after, aliases, checks, key=''):
    """Independently reverse only declared aliases and compare every input node.

    This checks the transformation itself, including dictionary key collisions,
    instead of only comparing two outputs of the same transformation.
    """
    reverse = {alias: original for table in aliases.tables.values() for original, alias in table.items()}
    def undo(text):
        return re.sub(r'<[a-z-]+-\d{3}>', lambda m: reverse.get(m[0], m[0]), text)
    def walk(original, public, field):
        if isinstance(original, dict):
            checks.check(isinstance(public, dict) and len(original) == len(public), 'object observations retained', 'preservation')
            for (original_key, original_value), (public_key, public_value) in zip(original.items(), public.items()):
                checks.check(undo(public_key) == original_key, 'object key retained', 'preservation')
                walk(original_value, public_value, original_key)
        elif isinstance(original, list):
            checks.check(isinstance(public, list) and len(original) == len(public), 'array observations retained', 'preservation')
            for original_value, public_value in zip(original, public):
                walk(original_value, public_value, field)
        elif field in PID_KEYS | {'start', 'process_start_ticks', 'uid'} and original is not None:
            checks.check(isinstance(public, str) and undo(public) == str(original), 'process identifier equality retained', 'preservation')
        elif isinstance(original, str):
            checks.check(isinstance(public, str) and undo(public) == original, 'text observations retained', 'preservation')
        else:
            checks.check(type(original) is type(public) and original == public, 'literal observation retained', 'preservation')
    walk(before, after, key)


def privacy(value, checks, label):
    text = json.dumps(value, ensure_ascii=False)
    patterns = {
        'unaliased home': r'/(?:home|Users)/[^/\s\"\x27]+',
        'unaliased session runtime': r'/run/user/\d+',
        'unaliased task temporary root': r'(?:/var/tmp/(?:nb|nc)\.|/tmp/agt-wl-)[A-Za-z0-9_-]+',
        'unaliased generated config': r'agterm-ghostty-[A-Za-z0-9]{6}(?![A-Za-z0-9-])',
        'unaliased UUID': r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b',
        'unaliased load address': r'\(0x[0-9a-fA-F]{8,}\)',
        'raw desktop assignment': r'(?:WAYLAND_DISPLAY|HYPRLAND_INSTANCE_SIGNATURE|DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR)\s*=\s*[^\s<]',
        'credential token': r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{24,})',
        'private key': r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
        'authorization header': r'(?i)Authorization:\s*(?:Bearer|Basic)\s+\S+',
    }
    for name, pattern in patterns.items():
        checks.check(not re.search(pattern, text), label + ': ' + name, 'privacy')


def load_inputs(root, checks):
    paths = [root / name for name in INPUT_FILES]
    checks.check(len(paths) == 38, 'original input set changed')
    inventory, raw, decoded = [], {}, {}
    for path in paths:
        checks.check(not path.is_symlink(), 'original is a symlink')
        name = path.relative_to(root).as_posix()
        data = path.read_bytes()
        inventory.append({'path': name, 'sha256': digest(data), 'bytes': len(data)})
        raw[name] = data
        if path.suffix == '.json':
            value = json.loads(data)
            if 'stdout' in value:
                stdout = value.pop('stdout')
                decoded[name] = {'original_file': name, 'original_sha256': digest(data),
                                 'transport': value, 'stdout_encoding': 'decoded JSON',
                                 'original_stdout_utf8_sha256': digest(stdout.encode()),
                                 'observation': json.loads(stdout)}
            else:
                decoded[name] = {'original_file': name, 'original_sha256': digest(data),
                                 'transport': None, 'stdout_encoding': 'direct JSON document',
                                 'observation': value}
    return inventory, raw, decoded


def literal_counts(text):
    patterns = {
        'test_pass': r'^✔ Test (?!run ).*passed',
        'suite_pass': r'^✔ Suite .*passed',
        'test_skip': r'^➜ Test .* skipped:',
        'test_fail': r'^✘ Test (?!run ).*failed',
        'suite_fail': r'^✘ Suite .*failed',
    }
    return {key: sum(bool(re.search(pattern, line)) for line in text.splitlines())
            for key, pattern in patterns.items()}


def text_binding(text, recorded_hash, recorded_bytes):
    data = text.encode()
    return {'recorded_guest_file_sha256': recorded_hash, 'recorded_guest_file_bytes': recorded_bytes,
            'collected_text_utf8_sha256': digest(data), 'collected_text_utf8_bytes': len(data),
            'collected_text_characters': len(text), 'collected_text_lines': len(text.splitlines()),
            'replacement_characters': text.count('\ufffd'),
            'byte_identity_to_recorded_guest_file': digest(data) == recorded_hash,
            'qualification': 'Collected text is not byte-identical to the recorded guest log. '
                             'The original collection JSON is hash-bound; normalization/decoding happened '
                             'before this publication. The recorded guest-file digest is retained as evidence, '
                             'not claimed independently recomputed from this collected text.'}


def derive(raw, decoded, repository, checks):
    def obs(name):
        return decoded[name]['observation']

    def check(condition, label):
        checks.check(condition, label)

    def command(status, label):
        rows = [c for c in status['commands'] if c['label'] == label]
        check(len(rows) == 1, 'unique command: ' + label)
        return rows[0]

    first_swift = obs('native-swift-toolchain-poll-private.json')
    swift = first_swift['status']
    compat = obs('native-swift-compat-result-private.json')
    check(swift['signature_verified'] is True, 'official toolchain signature retained')
    check(swift['expected_signing_fingerprint'] in swift['valid_signature_status'], 'signing fingerprint')
    check(swift['commands'][-1]['exit_code'] == 127 and swift['errors'], 'initial Swift load failure')
    check('libncurses.so.6' in first_swift['last_command_tail'], 'missing-library diagnosis')
    check(swift['archive_sha256'] == compat['toolchain_archive_sha256'], 'same toolchain archive after compatibility fix')
    check(compat['smoke_passed'] is True and compat['errors'] == [], 'compatible private Swift smoke')
    check(all(c['exit_code'] == 0 for c in compat['commands']), 'compatibility command exits')
    check(len(compat['dependencies']) == 2 and len(compat['private_library_hashes']) == 6, 'dependency identities')

    a1 = obs('native-full-build-attempt1-private.json')['status']
    a2_record = obs('native-full-build-attempt2-test-private.json')
    a2 = a2_record['report']
    a3 = obs('native-full-build-continuation3-poll-private.json')['status']
    complete = obs('native-full-build-complete-bindings-private.json')
    for index, a in enumerate([a1, a2, a3], 1):
        check(a['source_commit'] == SOURCE, 'attempt source ' + str(index))
        check(a['source_input_sha256'] == a1['source_input_sha256'], 'same 11 source inputs ' + str(index))
        check(a['toolchain_sha256'] == a1['toolchain_sha256'], 'same compiler identities ' + str(index))
    check(a1['toolchain_sha256']['swift-frontend'] == swift['swift_frontend_sha256'], 'build compiler binding')
    check(a1['passed'] is False and a1['phase'] == 'shutdown-queue', 'attempt1 failed before Swift suite')
    queue1 = command(a1, 'shutdown-queue')
    queue2 = command(a2, 'shutdown-queue')
    check(len(queue1['argv']) == 2 and queue1['exit_code'] == 1, 'omitted required queue argument')
    check(queue2['argv'][:2] == queue1['argv'] and len(queue2['argv']) == 3 and queue2['exit_code'] == 0,
          'queue invocation correction')
    check(a2['passed'] is False and a2['phase'] == 'swift-test', 'attempt2 failed in Swift suite')
    failed_test = command(a2, 'swift-test')
    failed_counts = literal_counts(a2_record['complete_log'])
    check(failed_counts == {'test_pass': 692, 'suite_pass': 98, 'test_skip': 0, 'test_fail': 1, 'suite_fail': 1},
          'attempt2 literal completion counts')
    check('Caught error: .pathTooLong' in a2_record['complete_log'], 'retained long socket fixture failure')
    check(a2['previous_attempt_sha256'] == digest(encoded(a1)), 'attempt1 to attempt2 status binding')
    check(a3['previous_attempt_sha256'] == digest(encoded(a2)), 'attempt2 to attempt3 status binding')
    check(a3['runtime_build_attempt_sha256'] == digest(encoded(a1)), 'native vendor build origin')
    check(complete['status_sha256'] == digest(encoded(a3)), 'staged status binding')
    check(a3['passed'] is True and a3['errors'] == [], 'attempt3 success')
    check(all(c['exit_code'] == 0 for c in a3['commands']), 'attempt3 command exits')
    passed_test = command(a3, 'swift-test')
    check('--skip-build' in passed_test['argv'], 'unchanged compiled suite retry')
    check(a3['test_and_build_temporary_root'].startswith('/var/tmp/nb.'), 'short temporary root')
    for label in ['swift-build', 'swift-release', 'stage', 'final-source-clean']:
        check(command(a3, label)['exit_code'] == 0, 'post-test native build/stage ' + label)
    check(len(a3['payload_sha256']) == complete['payload_file_count'] == 769, 'all staged file identities')
    check(all(a3['payload_sha256'][p] == h for p, h in complete['payload_hashes'].items()), 'four payload identities')
    summary_lines = [line['text'] for row in complete['logs'] if row['label'] == 'swift-test' for line in row['selected']]
    check(any('693 tests in 99 suites passed after 36.660 seconds' in t for t in summary_lines), 'attempt3 retained completion line')
    for key, record in a3['reused_runtime_build'].items():
        check(record == command(a1, key), 'original vendor build reused: ' + key)
    for key, record in a3['prior_regression_commands'].items():
        check(record == command(a2, key), 'corrected regressions retained: ' + key)

    core = obs('native-core-test-result-private.json')
    core_status = core['status']
    core_command = command(core_status, 'core-tests')
    check(core_status['source_commit'] == SOURCE and core_status['passed'] and core_status['errors'] == [], 'native core source/pass')
    check('--no-parallel' in core_command['argv'] and '--filter' not in core_command['argv'] and '--skip' not in core_command['argv'],
          'unfiltered native core invocation')
    check(len(core['logs']) == 1 and core['logs'][0]['label'] == 'core-tests', 'retained full collected core text')
    core_text = core['logs'][0]['text']
    core_counts = literal_counts(core_text)
    check(core_counts == {'test_pass': 4104, 'suite_pass': 179, 'test_skip': 1, 'test_fail': 0, 'suite_fail': 0}, 'literal core completion counts')
    skips = [line for line in core_text.splitlines() if re.match(r'^➜ Test .* skipped:', line)]
    check('no non-POSIX login shell (fish) installed' in skips[0], 'exact missing-fish skip')
    check('4105 tests in 179 suites passed after 78.818 seconds' in core_text, 'core run headline')
    check(core['logs'][0]['sha256'] == core_command['log_sha256'], 'core guest-log identity binding')

    runtime = obs('native-current-source-runtime-result-private.json')['report']
    settings = runtime['settings']
    binding = obs('bound-77-native/binding.json')
    for key in ['source_commit', 'app_source_commit', 'library_source_commit']:
        check(settings[key] == SOURCE, 'complete source77 runtime ' + key)
    check(runtime['passed'] and runtime['errors'] == [], 'complete native runtime passed')
    check(runtime['diagnostic_only'] is True and settings['diagnostic_only'] is True, 'original diagnostic-only bounds retained')
    check(settings['build_status_sha256'] == complete['status_sha256'], 'runtime consumes complete staged build')
    check(settings['payload_hashes'] == complete['payload_hashes'], 'runtime four payload identities')
    check(settings['operator_review_url'] == REVIEW_URL, 'operator review binding')
    check(settings['operator_sha256'] == digest(raw['bound-77-native/native-run-template.py']) == binding['operator_sha256'], 'operator byte identity')
    check(settings['worker_sha256'] == digest(raw['native-current-source-runtime-worker.py']), 'runtime worker byte identity')
    for name, h in binding['assets'].items():
        check(digest(raw['bound-77-native/' + name]) == h, 'operator asset ' + name)
    for name, row in binding['checks'].items():
        check(row['exit_code'] == 0 and digest(raw['bound-77-native/' + name + '.log']) == row['log_sha256'],
              'retained synthetic helper check ' + name)
    for mode, count in [('none', 7), ('live', 9)]:
        m = runtime['modes'][mode]
        check(m['passed'] and m['result']['passed'] and len(m['result']['checks']) == count, 'runtime mode ' + mode)
        clean = m['independent_cleanup']
        check(clean['passed'] and not clean['forced_cleanup'] and not clean['timed_out'], 'normal cleanup ' + mode)
        check(clean['remaining_processes'] == clean['new_app_processes'] == clean['retained_recovery_paths'] == clean['cleanup_actions'] == clean['errors'] == [],
              'no cleanup recovery ' + mode)
        check(m['result']['identity']['harness']['sha256'] == binding['assets']['harness.sh'], 'harness identity ' + mode)
    check(len(runtime['config_repetitions']) == 5, 'five fresh configuration repetitions')
    for i, repetition in enumerate(runtime['config_repetitions'], 1):
        p = repetition['probe']
        check(repetition['passed'] and p['passed'] and len(p['checks']) == 4, 'config checks ' + str(i))
        check(not p['forced_recovery'] and p['signals'] == p['cleanup_actions'] == p['errors'] == [], 'no config recovery ' + str(i))
        check(p['noisy_titles_written_before_shutdown'] == 100 and p['noisy_producer']['tagged'] and
              p['noisy_producer']['state'] not in {'Z', 'X'}, 'live noisy producer ' + str(i))
        check(repetition['remaining_owned_processes'] == p['remaining_processes'] == [], 'no remaining private processes ' + str(i))
        check(p['sentinel_preserved'] and p['socket_directory_removed'] and p['remaining_generated_paths'] == [], 'config cleanup/preservation ' + str(i))
        check(p['caller_environment_unchanged'] and p['login_shell_unchanged'], 'config environment preserved ' + str(i))
    observer = runtime['image_observer']
    check(observer['completed'] and observer['errors'] == [] and observer['missing_expected_images'] == [], 'completed image observation')
    check(len(observer['observed_images']) == observer['expected_image_count'] == 13, '13 frontend identities')
    check(sum(x['mapping_count'] for x in observer['observed_images']) == 91, '91 library map rows')
    for entry in observer['observed_images']:
        check(entry['app_sha256'] == complete['payload_hashes']['bin/agterm-linux.bin'] and
              entry['library_sha256'] == complete['payload_hashes']['lib/libghostty.so'], 'observed actual app/library identity')
    preservation_keys = ['payload_unchanged', 'frozen_payload_unchanged', 'installed_payload_unchanged', 'shared_runtime_unchanged', 'login_shell_unchanged']
    check(all(runtime[key] is True for key in preservation_keys), 'all final preservation predicates')
    check(runtime['preservation'] == {'passed': True, 'protected_process_count': 2, 'lost': {}}, 'two protected processes survived')
    summary = obs('native-current-source-runtime-summary-private.json')
    check(summary['original_sha256'] == digest(raw['native-current-source-runtime-result-private.json']), 'derived runtime summary bound to original')

    # These are source reads only. No imported code, shell, compiler or VM calls.
    source_bindings = []
    pins = [(SOURCE, path, h) for path, h in a3['source_input_sha256'].items()]
    pins += [(SOURCE, 'scripts/test-omarchy.sh', binding['assets']['harness.sh']),
             (PROBE_SOURCE, 'scripts/omarchy-config-probe.py', binding['assets']['config-probe.py'])]
    for commit, path, expected_hash in pins:
        result = subprocess.run(['git', '-C', str(repository), 'cat-file', 'blob', commit + ':' + path],
                                check=True, capture_output=True, timeout=20)
        data = result.stdout
        checks.check(digest(data) == expected_hash, 'pinned source bytes: ' + path, 'git_binding')
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        source_bindings.append({'commit': commit, 'path': path, 'git_blob': blob, 'sha256': digest(data), 'bytes': len(data)})
    static_assertions = {
        'native-full-build-worker-continuation3.py': ["tempfile.mkdtemp(prefix='nb.',dir='/var/tmp')", "'--skip-build'"],
        'native-core-test-worker.py': ["'--no-parallel'", "'agtermCore'"],
        'native-current-source-runtime-worker.py': ["LIBGL_ALWAYS_SOFTWARE='1'", 'payload_sha256', 'image_observer'],
    }
    for name, snippets in static_assertions.items():
        source_text = raw[name].decode()
        for snippet in snippets:
            check(snippet in source_text, 'static worker assertion: ' + name + ' ' + snippet)

    return {
        'toolchain': {'initial_signature_verified_but_version_exit': 127, 'compatible_smoke_passed': True,
                      'archive_sha256': swift['archive_sha256'], 'swift_frontend_sha256': swift['swift_frontend_sha256'],
                      'compiler_version': compat['version_output'], 'private_dependencies': compat['dependencies']},
        'build': {'source_commit': SOURCE, 'attempt1': 'missing required queue argument; operator invocation error',
                  'attempt2_literal_counts': failed_counts,
                  'attempt2_failure': 'SharedCommandProcessTests.swift:67:6 socket precedence fixture: .pathTooLong',
                  'attempt2_collected_log': text_binding(a2_record['complete_log'], failed_test['log_sha256'], failed_test['log_bytes']),
                  'attempt3': 'same source/inputs/compiler and unchanged compiled test suite with fresh short TMPDIR and --skip-build; then debug/release/stage',
                  'attempt3_retained_headline': summary_lines[-1],
                  'attempt3_count_basis': 'Retained original completion excerpt plus command exit/status; a full third-attempt test log was not collected in these inputs, so no new literal693 recount is claimed.',
                  'staged_files': 769, 'source_input_files': 11,
                  'attempt_status_sha256': [digest(encoded(a)) for a in [a1, a2, a3]],
                  'payload_hashes': complete['payload_hashes']},
        'core': {'source_commit': SOURCE, 'literal_counts': core_counts, 'skip_line': skips[0],
                 'collected_log': text_binding(core_text, core_command['log_sha256'], core_command['log_bytes'])},
        'runtime': {'source_commit': SOURCE, 'mode_check_counts': {'none': 7, 'live': 9},
                    'configuration_check_counts': [len(x['probe']['checks']) for x in runtime['config_repetitions']],
                    'noisy_shutdown_seconds': [x['probe']['apps'][1]['shutdown_seconds'] for x in runtime['config_repetitions']],
                    'frontend_identities': 13, 'library_mapping_rows': 91,
                    'protected_processes': 2, 'preservation': {key: runtime[key] for key in preservation_keys},
                    'seconds': runtime['ended_at'] - runtime['started_at'],
                    'rendering': 'software GL', 'Live_shell': 'process-local zsh fixture; real login shell remains bash',
                    'diagnostic_only_original_flag': True, 'exact_public_result': RUNTIME_URL},
        'source_bindings': source_bindings,
    }


def expected_package(root, repository, review_record, checks):
    inventory, raw, decoded = load_inputs(root, checks)
    derived = derive(raw, decoded, repository, checks)
    aliases = Aliases()
    for record in decoded.values():
        aliases.seed(record)
    result = {}
    group_keys = ['toolchain', 'build', 'core', 'runtime']
    for (prefix, filename), group in zip(GROUPS.items(), group_keys):
        records = []
        for name, record in decoded.items():
            if name.startswith(prefix):
                public_record = aliases.value(record)
                preserved_observations(record, public_record, aliases, checks)
                records.append(public_record)
        result[filename] = {'schema': SCHEMA, 'scope': SCOPE, 'records': records, 'derived': aliases.value(derived[group])}
    binding = aliases.value(decoded['bound-77-native/binding.json'])
    preserved_observations(decoded['bound-77-native/binding.json'], binding, aliases, checks)
    helper_logs = [{'original_file': name, 'original_sha256': digest(data), 'text': aliases.text(data.decode())}
                   for name, data in raw.items() if name.startswith('bound-77-native/') and name.endswith('.log')]
    for row in helper_logs:
        preserved_observations(raw[row['original_file']].decode(), row['text'], aliases, checks)
    source_files = [entry for entry in inventory if entry['path'].endswith(('.py', '.sh'))]
    review = json.loads(review_record.read_bytes())
    checks.check(review['html_url'] == REVIEW_URL, 'public operator review URL')
    checks.check('58' in review['body'] and 'same-account' in review['body'], 'same-account static review bounds')
    result['native-bindings.json'] = {
        'schema': SCHEMA, 'scope': SCOPE, 'operator_binding': binding, 'retained_helper_logs': helper_logs,
        'worker_operator_and_test_sources': source_files, 'pinned_git_source_bindings': derived['source_bindings'],
        'review': {'url': review['html_url'], 'id': review['id'], 'created_at': review['created_at'],
                   'updated_at': review['updated_at'], 'body_sha256': digest(review['body'].encode()),
                   'qualification': 'Existing 58 synthetic-method same-account review; not new runtime execution or independent account approval.'},
    }
    declaration = aliases.declaration()
    for name in result:
        result[name]['sanitization'] = declaration
    result['public-operator-review.json'] = review
    for name, data in result.items():
        privacy(data, checks, name)
    return result, inventory, declaration, raw


def negative_controls(expected):
    """Only mutate in-memory copies; never alter original or deliverable files."""
    tests = []
    def comparison(name, mutate):
        item = copy.deepcopy(expected['native-runtime.json'])
        mutate(item)
        try:
            Checks().compare(expected['native-runtime.json'], item, 'negative-control')
        except ValueError:
            tests.append(name)
        else:
            raise ValueError('negative control accepted: ' + name)
    comparison('changed numeric observation', lambda x: x['derived'].__setitem__('library_mapping_rows', 90))
    comparison('removed observation field', lambda x: x['records'][1]['observation'].pop('report'))
    comparison('removed array element', lambda x: x['derived']['configuration_check_counts'].pop())
    for label, value in [('personal path', '/home/private-person/session'),
                         ('raw desktop assignment', 'WAYLAND_DISPLAY=private-display'),
                         ('credential-shaped synthetic token', 'ghp_' + 'A' * 36)]:
        try:
            privacy({'injected': value}, Checks(), 'negative-control')
        except ValueError:
            tests.append(label)
        else:
            raise ValueError('privacy negative control accepted: ' + label)
    return tests


def verify(root, repository, package):
    checks = Checks()
    expected, originals, aliases, raw = expected_package(root, repository, package / 'public-operator-review.json', checks)
    manifest = json.loads((package / 'package-manifest.json').read_bytes())
    expected_names = set(expected) | {'README.native-complete-insertion.md', 'verify-native-complete.py', 'verification.json'}
    checks.check(manifest['schema'] == SCHEMA and manifest['scope'] == SCOPE, 'manifest schema and scope', 'manifest')
    checks.check({e['path'] for e in manifest['deliverables']} == expected_names and
                 len(manifest['deliverables']) == len(expected_names), 'exact deliverable set', 'manifest')
    checks.check({p.name for p in package.iterdir()} == expected_names | {'package-manifest.json'}, 'no unmanifested package entries', 'manifest')
    checks.compare(originals, manifest['originals'], 'original inventory')
    for name, value in expected.items():
        actual = json.loads((package / name).read_bytes())
        checks.compare(value, actual, name)
        privacy(actual, checks, name + ' readback')
    for entry in manifest['deliverables']:
        path = package / entry['path']
        checks.check(path.is_file() and not path.is_symlink(), 'deliverable type', 'manifest')
        checks.check(digest(path.read_bytes()) == entry['sha256'] and path.stat().st_size == entry['bytes'], 'deliverable digest ' + entry['path'], 'manifest')
    privacy((package / 'README.native-complete-insertion.md').read_text(), checks, 'README')
    for name, data in raw.items():
        checks.check((root / name).read_bytes() == data, 'original changed during read-only verification', 'immutable')
    controls = negative_controls(expected)
    return {'schema': SCHEMA, 'passed': True, 'checks': dict(sorted(checks.counts.items())),
            'original_files': len(originals), 'collected_json_records': 20,
            'bound_operator_json_records': 1, 'retained_helper_logs': 3,
            'worker_operator_test_source_files': len(expected['native-bindings.json']['worker_operator_and_test_sources']),
            'pinned_git_source_bindings': 13, 'negative_controls_rejected': controls,
            'alias_categories': aliases['categories'], 'scope': SCOPE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root', type=Path, required=True)
    parser.add_argument('--repository', type=Path, required=True, help='local object database containing source77 and the probe commit; read only')
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        result = verify(args.original_root, args.repository, args.package)
        require(result == json.loads((args.package / 'verification.json').read_bytes()), 'recorded verifier results differ')
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        # Do not echo raw private values or subprocess stderr in a failure report.
        print(json.dumps({'passed': False, 'error_type': type(error).__name__, 'detail': str(error) if isinstance(error, ValueError) else 'input or binding check failed'}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
