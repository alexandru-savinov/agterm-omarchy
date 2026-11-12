#!/usr/bin/env python3
"""Read-only comparison of a public extract with retained Mac diagnostics.

Standard library only. Never imports an original helper, spawns a process,
contacts a service, reads credentials, or writes a file. All observations are
historical; checking the evidence is not another runtime or signature check.
"""
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import re
import stat

INDEX_SHA256 = '8955c4d110baf5c46db0bbf330d441256ef7499ff934c8db0629699908a3160e'
PREPARED = 'scanner-admin-stop-prepared-sf79c3480ffb'
CORRECTED = 'source-admin-stop-corrected-sr305e1111'
BUNDLE = 'scanner-admin-stop-bundle-sf79c3480ffb'
CONTEXT = 'scanner-zmx-context-diagnostic-sf79c3480ffb'
DIAGNOSTIC = 'mac-private-zmx-diagnostic-1'
RUN = CONTEXT + '/runs/root-context-01'
ZMX = '4db635396d83ded73e102fb365375a9216c01da515d58c796537b83210b6eb01'
SCHEMA = 'agterm-mac-admin-context-publication/1'
RECEIPT_COUNTS = {'mac-admin-prerequisites.json': 9, 'mac-admin-bundle-retry.json': 6,
                  'mac-zmx-context.json': 10, 'mac-admin-provenance.json': 5,
                  'selected-log-projections.json': 20}
BOUNDARY = ('Historical prerequisite/context diagnostics only. No host administrative-stop request '
            'or stopped reply was reached. No product stop failure/pass, OS-policy cause, '
            'current-source app build, native release acceptance or independent approval is established.')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode()


def require(value, label):
    if not value:
        raise ValueError(label)


class Checks:
    def __init__(self):
        self.counts = collections.Counter()

    def check(self, value, label, category='evidence'):
        require(value, label)
        self.counts[category] += 1

    def compare(self, expected, actual, label, category='comparison'):
        self.check(type(expected) is type(actual), 'type: ' + label, category)
        if isinstance(expected, dict):
            self.check(list(expected) == list(actual), 'keys/order: ' + label, category)
            for key in expected:
                self.compare(expected[key], actual[key], label + '/' + key, category)
        elif isinstance(expected, list):
            self.check(len(expected) == len(actual), 'length: ' + label, category)
            for index, value in enumerate(expected):
                self.compare(value, actual[index], label + '/' + str(index), category)
        else:
            self.check(expected == actual, 'value: ' + label, category)


class Aliases:
    """Shared aliases with numeric identities restricted to declared positions.

    Reversibility alone does not establish that a number was a process ID.
    Generic strings and keys never receive numeric substitutions.
    Original values and this map stay private.
    """
    identity_keys = {'pid', 'ppid', 'parent', 'host_pid', 'peer_pid'}
    error_paths = {
        ('mac-admin-prerequisites.json', 'synthetic_attempts', '*', 'value', 'errors', '*'),
        ('mac-admin-bundle-retry.json', 'retry_result', 'value', 'errors', '*'),
        ('mac-admin-bundle-retry.json', 'retained_result_projection', 'value', 'reported_errors', '*'),
        ('selected-log-projections.json', 'logs', '*', 'selected_lines', '*', 'text'),
    }
    sample_path = ('mac-admin-prerequisites.json', 'fifteen_second_diagnostic',
                   'value', 'sample', 'stderr')

    def __init__(self, root):
        self.tables = collections.defaultdict(dict)
        self.replacements = collections.Counter()
        self.fixed = {'/Applications/agterm.app': '<installed-release-bundle>'}

    def alias(self, kind, value):
        table = self.tables[kind]
        if str(value) not in table:
            table[str(value)] = '<' + kind + '-' + f'{len(table) + 1:03d}' + '>'
        self.replacements[kind] += 1
        return table[str(value)]

    def seed(self, value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in self.identity_keys and type(child) is int:
                    self.alias('pid', child)
                elif key == 'uid' and type(child) is int:
                    self.alias('uid', child)
                self.seed(child)
        elif isinstance(value, list):
            for child in value:
                self.seed(child)
        elif isinstance(value, str):
            for original in re.findall(r'/Users/[^/\s\"\x27<>]+/output/agterm-omarchy/mac-vm-validation-\d{8}', value):
                self.fixed[original] = '<validation-output-root>'

    def text(self, value):
        for original, alias in self.fixed.items():
            value = value.replace(original, alias)
        patterns = [('stage', r'/private/tmp/(?:ast-|azc-|azm\.)[A-Za-z0-9_.-]+'),
                    ('host-home', r'/Users/[^/\s\"\x27<>\)\],]+'),
                    ('synthetic-bundle-id', r'org\.agterm\.fixture\.adminstop\.[0-9a-f]{32}'),
                    ('load-address', r'0x[0-9a-fA-F]{8,}'),
                    ('sample-thread', r'Thread_\d+')]
        for kind, pattern in patterns:
            value = re.sub(pattern, lambda match: self.alias(kind, match[0]), value)
        return value

    def known_pid(self, number):
        require(number in self.tables['pid'], 'text/key PID lacks a typed identity binding')
        return self.tables['pid'][number]

    def pid_text(self, value, path):
        patterns = []
        if path in self.error_paths:
            patterns = [r'(?m)^(TimeoutError: owned process did not exit: )([0-9]+)(?=\r?$)']
        elif path == self.sample_path:
            patterns = [r'(?m)^(Sampling process )([0-9]+)(?= for )',
                        r'(?m)^(Sample analysis of process )([0-9]+)(?= written to file )']
        for pattern in patterns:
            value = re.sub(pattern, lambda match: match[1] + self.known_pid(match[2]), value)
        return value

    def walk(self, value, key=None, path=()):
        if isinstance(value, dict):
            # These result dictionaries are explicitly keyed by process ID.
            # Numeric dictionary keys elsewhere are ordinary metadata.
            if key == 'exit_codes':
                require(all(k.isdecimal() for k in value), 'non-numeric original exit-code PID key')
                return {self.known_pid(k): self.walk(v, k, path + (k,)) for k, v in value.items()}
            return {self.text(k): self.walk(v, k, path + (k,)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.walk(item, key, path + ('*',)) for item in value]
        if type(value) is int and key in self.identity_keys:
            return self.alias('pid', value)
        if type(value) is int and key == 'uid':
            return self.alias('uid', value)
        return self.pid_text(self.text(value), path) if isinstance(value, str) else value

    def reverse_text(self, value):
        pairs = [(alias, original) for original, alias in self.fixed.items()]
        pairs += [(alias, original) for table in self.tables.values() for original, alias in table.items()]
        for alias, original in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
            value = value.replace(alias, original)
        return value

    def reverse(self, value, key=None):
        if isinstance(value, dict):
            return {self.reverse_text(k): self.reverse(v, self.reverse_text(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.reverse(item) for item in value]
        if isinstance(value, str):
            original = self.reverse_text(value)
            if key in self.identity_keys or key == 'uid':
                return int(original) if original.isdigit() else original
            return original
        return value


def read_inputs(root, public, checks):
    raw_index = (public / 'original-bindings.json').read_bytes()
    checks.check(digest(raw_index) == INDEX_SHA256, 'frozen original index', 'input')
    index = json.loads(raw_index)
    checks.check(index['schema'] == 'agterm-mac-admin-context-originals/1', 'original index schema', 'schema')
    checks.check(index['file_count'] == len(index['files']) == 2089, 'original count', 'input')
    data = {}
    for relative, receipt in index['files'].items():
        path = Path(relative)
        checks.check(not path.is_absolute() and '..' not in path.parts, 'relative input path', 'input')
        raw = (root / path).read_bytes()
        checks.check(len(raw) == receipt['bytes'], 'input bytes: ' + relative, 'input')
        checks.check(digest(raw) == receipt['sha256'], 'input digest: ' + relative, 'input')
        # Keep only the small evidence files in memory; large payloads need hashes only.
        if len(raw) < 2_000_000 and path.suffix in {'.json', '.py', '.txt', '.log', '.stdout', '.stderr'}:
            data[relative] = raw
    return index, data


def make_documents(root, public, checks):
    index, raw = read_inputs(root, public, checks)
    def load(path):
        return json.loads(raw[path])
    def receipt(path):
        return {'path': path, **index['files'][path]}
    def projection(path, keys):
        source = load(path)
        return {'original': receipt(path), 'projection': 'selected top-level JSON fields',
                'selected_keys': keys, 'value': {key: source[key] for key in keys}}
    def observed(path):
        return {'original': receipt(path), 'projection': 'complete parsed JSON document, aliases only',
                'value': load(path)}

    synthetic_paths = [CORRECTED + '/runs/root-stop-' + n + '/result.json' for n in ['01', '02']]
    bundle_path = BUNDLE + '/runs/root-bundle-stop-01/result.json'
    prior_standalone_path = 'mac-private-zmx-standalone-diagnostic-private.json'
    result_path = RUN + '/result.json'
    simple_preparation_keys = ['state', 'upstream_declared_ref', 'client_build_ref', 'source',
                               'binary_sha256', 'budgets_seconds', 'source_binding_limit']
    prior = {'schema': SCHEMA, 'boundary': BOUNDARY,
             'synthetic_attempts': [observed(path) for path in synthetic_paths],
             'fifteen_second_diagnostic': observed(DIAGNOSTIC + '/result-private.json'),
             'earlier_standalone_observation': observed(prior_standalone_path),
             'original_preparation': projection(PREPARED + '/manifest.json', simple_preparation_keys),
             'corrected_preparation': projection(CORRECTED + '/manifest.json', simple_preparation_keys),
             'driver_receipts': {
                 'original_preparation_only': receipt(PREPARED + '/driver.py'),
                 'both_executed_synthetic_attempts': receipt(CORRECTED + '/driver.py'),
                 'fifteen_second_sampled_diagnostic': receipt('diagnose-mac-private-zmx.py'),
                 'earlier_standalone': {'available': False, 'driver_sha256': None, 'deadline_seconds': None,
                     'boundary': 'The retained result binds argv, elapsed time and image, but this finite input set contains no separate driver/command artifact or deadline receipt for that earlier observation. The later comparison driver is not substituted.'}}}
    bundle = {'schema': SCHEMA, 'boundary': BOUNDARY,
              'retry_result': observed(bundle_path),
              'bundle_contract': observed(BUNDLE + '/bundle-contract.json'),
              'signature_preparation': observed(BUNDLE + '/codesign-preparation.json'),
              'preparation': projection(BUNDLE + '/manifest.json', simple_preparation_keys +
                  ['driver_sha256', 'parent_driver_sha256', 'signature_boundary', 'declared_zmx_ref']),
              'driver_receipt': receipt(BUNDLE + '/driver.py'),
              'retained_result_projection': observed(CONTEXT + '/latest-root-result.json')}
    context = {'schema': SCHEMA, 'boundary': BOUNDARY,
               'comparison_result': observed(result_path),
               'context_bindings': observed(CONTEXT + '/context-bindings.json'),
               'signature_preparation': observed(CONTEXT + '/signature-review.json'),
               'preparation_comparison': observed(CONTEXT + '/preparation-comparison.json'),
               'preparation': projection(CONTEXT + '/manifest.json', ['case_order', 'observation_seconds',
                   'term_observation_seconds', 'kill_observation_seconds', 'utility_timeout_seconds',
                   'new_sampling', 'sampling_reason', 'aggregate_signature_boundary', 'source_refs',
                   'source_standalone', 'diagnostic_only', 'administrative_stop_acceptance']),
               'driver_receipt': receipt(CONTEXT + '/driver.py'),
               'identity_adapter_receipt': receipt(CONTEXT + '/identity.py'),
               'case_result_receipts': {case: receipt(RUN + '/' + case + '/result.json')
                                        for case in ['standalone', 'pristine', 'with-peer']}}
    provenance = {'schema': SCHEMA, 'boundary': BOUNDARY,
                  'release_artifact_observation': observed('mac-release-provenance/result-private.json'),
                  'release_metadata': observed('mac-release-provenance/release-metadata.json'),
                  'release_archive_receipt': receipt('mac-release-provenance/agterm-0.38.1.dmg'),
                  'source_equivalence': observed(BUNDLE + '/source-equivalence.json'),
                  'source_binding_refresh': observed(CONTEXT + '/source-binding-refresh.json'),
                  'input_set': {'index': 'original-bindings.json', 'sha256': INDEX_SHA256,
                               'files': index['file_count'], 'bytes': index['total_bytes']},
                  'historical_execution_operator': 'root_mac_vm', 'publication_preparer': 'retry_audit',
                  'new_runtime_or_compiler_execution': False, 'same_account_not_independent_approval': True}

    # The complete run JSONs above preserve every field. Text files below are
    # explicitly selected projections; the full bytes remain in the original index.
    log_files = []
    for path in synthetic_paths + [bundle_path]:
        parent = str(Path(path).parent)
        log_files += [parent + '/exception.txt', parent + '/inventory-before-host.stdout',
                      parent + '/inventory-before-host.stderr']
    log_files += [str(Path(bundle_path).parent) + '/bundle-copy.' + suffix for suffix in ['stdout', 'stderr']]
    log_files += [DIAGNOSTIC + '/' + name for name in ['stdout.log', 'stderr.log', 'sample-private.txt']]
    log_files += [RUN + '/' + case + '/' + stream + '.log'
                  for case in ['standalone', 'pristine', 'with-peer'] for stream in ['stdout', 'stderr']]
    logs = {'schema': SCHEMA, 'projection_boundary': 'Selected UTF-8 lines with original line endings. '
            'Aliases are reversible; omitted traceback/sample lines are not published or claimed as complete logs.',
            'logs': []}
    for path in log_files:
        text = raw[path].decode('utf-8')
        lines = text.splitlines(keepends=True)
        if path.endswith('exception.txt'):
            chosen = [i for i, line in enumerate(lines, 1) if line.startswith('TimeoutError:')]
            reason = 'Final exception line only; traceback intentionally omitted.'
        elif path.endswith('sample-private.txt'):
            chosen = [i for i, line in enumerate(lines, 1)
                      if line.startswith('Physical footprint') or '_dyld_start' in line
                      or line.startswith('Binary images description not available')]
            reason = 'Physical footprint and dyld-only observation; host/process/address header and unrelated sample metadata intentionally omitted.'
        else:
            chosen = list(range(1, len(lines) + 1))
            reason = 'Entire retained stream, including genuinely empty streams.'
        logs['logs'].append({'original': receipt(path), 'encoding': 'UTF-8 strict',
                             'original_line_count': len(lines), 'complete_stream': len(chosen) == len(lines),
                             'selection_reason': reason,
                             'selected_lines': [{'line': i, 'text': lines[i - 1]} for i in chosen]})

    # Evidence checks are separate from a mechanical JSON comparison.
    for path in synthetic_paths + [bundle_path]:
        result = load(path)
        checks.check(result['passed'] is False and result['forced_cleanup'] is True, 'failure retained ' + path)
        checks.check(len(result['executed_argv']) == 1 and result['executed_argv'][0][0].endswith('/zmx')
                     and result['executed_argv'][0][1:] == ['list'], 'only prerequisite zmx list executed')
        checks.check(not any(key in result for key in ['host_pid', 'peer_pid', 'stop_response']), 'host/peer/stop not reached')
        checks.check(list(result['exit_codes'].values()) == [-15], 'actual prerequisite exit -15')
        checks.check([step['signal'] for step in result['recovery']] == [15], 'TERM only; no actual KILL')
        checks.check(result['recovery'][0]['identity'] == result['owned'][0], 'recovery owns exact recorded child')
        checks.check(result['private_inventory_complete'] is True and result['private_processes_remaining'] == [], 'complete empty final inventory')
        checks.check(all(item['passed'] is True for item in result['checks']), 'recorded preservation checks pass')
        evidence = load(str(Path(path).parent) + '/evidence-manifest.json')
        checks.check(evidence['passed'] is False, 'run evidence remains failed')
        driver = CORRECTED if path in synthetic_paths else BUNDLE
        checks.check(evidence['driver_sha256'] == receipt(driver + '/driver.py')['sha256'], 'executed driver binding')
        checks.check(evidence['preparation_manifest_sha256'] == receipt(driver + '/manifest.json')['sha256'], 'preparation binding')
        checks.check(load(driver + '/manifest.json')['budgets_seconds']['inventory'] == 3, 'fixed original 3s prerequisite')
    diagnostic = load(DIAGNOSTIC + '/result-private.json')
    checks.check(diagnostic['signals'] == [15] and diagnostic['exit_code'] == -15, '15s sampled diagnostic TERM')
    checks.check(diagnostic['seconds'] == 15.00586199760437 and diagnostic['sample']['exit_code'] == 0, 'retained sampled timing')
    checks.check(diagnostic['process_gone'] is True and diagnostic['namespace_empty'] is True, 'sampled diagnostic cleanup')
    standalone = load(prior_standalone_path)
    checks.check(standalone['exit_code'] == 0 and standalone['signals'] == [] and standalone['process_gone'] is True, 'earlier standalone result')
    checks.check(standalone['namespace_empty'] is False, 'retain earlier nonempty namespace observation')
    checks.check(standalone['stdout'] == '' and standalone['stderr'] == 'no sessions found in ' + standalone['stage'] + '/z\n', 'earlier exact empty-session inventory')
    result = load(result_path)
    checks.check(result['diagnostic_only'] is True and result['administrative_stop_acceptance'] is False, 'diagnostic acceptance boundary')
    checks.check(result['driver_sha256'] == receipt(CONTEXT + '/driver.py')['sha256'], 'comparison driver binding')
    checks.check(result['identity_adapter_sha256'] == receipt(CONTEXT + '/identity.py')['sha256'], 'identity adapter binding')
    checks.check(result['preparation_manifest_sha256'] == receipt(CONTEXT + '/manifest.json')['sha256'], 'comparison preparation binding')
    for name in ['comparison_complete', 'all_image_identities_observed', 'installed_attributes_unchanged',
                 'installed_bundle_unchanged', 'originals_unchanged', 'protected_preserved']:
        checks.check(result[name] is True, 'recorded ' + name)
    checks.check(result['all_private_survivors'] == [] and result['errors'] == [] and result['signals_received'] == [], 'comparison complete cleanup')
    cases = result['cases']
    checks.check(cases['standalone']['started_at'] < cases['pristine']['started_at'] < cases['with-peer']['started_at'], 'fixed actual order')
    for name, case in cases.items():
        checks.compare(case, load(RUN + '/' + name + '/result.json'), 'separate case equality/' + name, 'cross_binding')
        checks.check(case['observation_seconds'] == 3.0 and case['image_sha256'] == ZMX, 'same image and 3s bound')
        checks.check(case['diagnostic_only'] is True and case['administrative_stop_acceptance'] is False, 'case scope')
        checks.check(case['cleanup_complete'] is True and case['private_survivors'] == [] and case['remaining_direct_children'] == [], 'case cleanup')
        checks.check(case['context_after_matches'] is True and case['attributes_after_match'] is True, 'case context/attributes')
        for stream in ['stdout', 'stderr']:
            source = raw[RUN + '/' + name + '/' + stream + '.log']
            checks.check(source.decode('utf-8') == case[stream] and digest(source) == case[stream + '_sha256'], 'raw stream binding')
        if name == 'standalone':
            checks.check(case['normal_empty_inventory'] is True and case['exit_code_before_cleanup'] == 0
                         and case['recovery'] == [] and case['observed_seconds'] < 3, 'standalone success within bound')
            checks.check(case['stderr'] == 'no sessions found in ' + case['namespace'] + '\n' and case['stdout'] == '', 'exact comparison empty inventory')
        else:
            checks.check(case['normal_empty_inventory'] is False and case['status'] == 'observation-timeout'
                         and case['exit_code_before_cleanup'] is None and case['observed_seconds'] >= 3, 'bundle timeout retained')
            checks.check([step['signal'] for step in case['recovery']] == [15]
                         and [item['exit_code'] for item in case['processes']] == [-15], 'bundle actual cleanup')
            checks.check(case['recovery'][0]['identity'] == case['direct_image_identity'], 'comparison exact recovery identity')
    signatures = load(CONTEXT + '/signature-review.json')['checks']
    checks.check([(item['label'], item['exit_code']) for item in signatures] ==
                 [('pristine bundle', 0), ('with-peer bundle', 1), ('pristine zmx', 0), ('with-peer zmx', 0), ('standalone zmx', 0)], 'recorded aggregate/individual signature boundary')
    pristine = load(CONTEXT + '/pristine-tree.json')
    augmented = load(CONTEXT + '/with-peer-tree.json')
    pristine_map = {item['path']: item for item in pristine}
    augmented_map = {item['path']: item for item in augmented}
    checks.check(len(pristine) == 669 and sum(item['kind'] == 'file' for item in pristine) == 640, 'complete original input tree')
    checks.check(set(augmented_map) - set(pristine_map) == {'Contents/MacOS/session-host-test-client'}, 'sole disclosed addition')
    for path, item in pristine_map.items():
        checks.compare(item, augmented_map[path], 'original tree entry/' + path, 'tree_preservation')
    checks.check(len(load(CONTEXT + '/preserved-originals.json')) == 751, 'historical preserved input count')
    for name in ['standalone', 'pristine', 'with-peer']:
        binding = load(CONTEXT + '/context-bindings.json')['cases'][name]
        for item in load(CONTEXT + '/' + binding['tree']):
            relative = CONTEXT + '/' + binding['input']
            if item['path'] != '.':
                relative += '/' + item['path']
            path = root / relative
            mode = path.lstat().st_mode
            checks.check(stat.S_IMODE(mode) == item['mode'], 'retained input mode', 'input_tree')
            if item['kind'] == 'file':
                checks.check(stat.S_ISREG(mode), 'retained regular input', 'input_tree')
                checks.check(index['files'][relative]['sha256'] == item['sha256']
                             and index['files'][relative]['bytes'] == item['size'], 'input tree content binding', 'input_tree')
            elif item['kind'] == 'directory':
                checks.check(stat.S_ISDIR(mode), 'retained input directory', 'input_tree')
            else:
                checks.check(item['kind'] == 'symlink' and stat.S_ISLNK(mode), 'retained input symlink', 'input_tree')
                checks.check(str(path.readlink()) == item['target'], 'retained input link target', 'input_tree')
        attribute_file = CONTEXT + '/' + name + '-attributes.txt'
        checks.check(index['files'][attribute_file]['sha256'] == binding['attribute_sha256'],
                     'recorded normalized attributes binding', 'input_tree')
    release = load('mac-release-provenance/result-private.json')
    checks.check(release['passed'] is True and release['images_match_release'] is True
                 and release['mounted_read_only'] is True and release['detached'] is True,
                 'historical official release comparison')
    checks.check(release['actual_sha256'] == release['expected_sha256'] ==
                 receipt('mac-release-provenance/agterm-0.38.1.dmg')['sha256'], 'release archive bytes binding')
    for name, identity in load(BUNDLE + '/manifest.json')['binary_sha256'].items():
        if name in release['binary_comparisons']:
            row = release['binary_comparisons'][name]
            checks.check(row['equal'] is True and row['release_sha256'] == row['installed_sha256'] == identity,
                         'retained release/helper image link')
    for row in load(BUNDLE + '/manifest.json')['source']:
        data = (root / BUNDLE / 'source' / row['path']).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        checks.check(digest(data) == row['sha256'] and blob == row['git_blob'], 'source byte/Git blob binding', 'source_binding')

    originals = {'mac-admin-prerequisites.json': prior, 'mac-admin-bundle-retry.json': bundle,
                 'mac-zmx-context.json': context, 'mac-admin-provenance.json': provenance,
                 'selected-log-projections.json': logs}
    aliases = Aliases(root)
    for document in originals.values():
        aliases.seed(document)
    documents = {name: aliases.walk(document, path=(name,)) for name, document in originals.items()}
    for name in originals:
        checks.compare(originals[name], aliases.reverse(documents[name]), name, 'reverse_alias')
    for name, document in documents.items():
        text = encoded(document).decode()
        for forbidden in [str(root), '/Users/', '/private/tmp/ast-', '/private/tmp/azc-', '/private/tmp/azm.',
                          '/Applications/agterm.app', 'org.agterm.fixture.adminstop.']:
            checks.check(forbidden not in text, 'private value absent: ' + name, 'privacy')
    return documents, aliases, index


def validate_public_shape(documents, index, checks):
    """Semantic checks independent of both alias generation and reversibility."""
    checks.check(set(documents) == set(RECEIPT_COUNTS), 'projected document set', 'schema')
    for name, document in documents.items():
        checks.check(document['schema'] == SCHEMA, 'literal publication schema: ' + name, 'schema')
        receipts = []
        def visit(value):
            if isinstance(value, dict):
                if set(value) == {'path', 'sha256', 'bytes'}:
                    receipts.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(document)
        checks.check(len(receipts) == RECEIPT_COUNTS[name], 'public receipt count: ' + name, 'original_resolution')
        for receipt in receipts:
            path = receipt['path']
            checks.check(path in index['files'], 'public original path resolves: ' + path, 'original_resolution')
            checks.check(receipt == {'path': path, **index['files'][path]},
                         'public original receipt matches: ' + path, 'original_resolution')


def check_numeric_collisions(checks):
    """PID 1 is deliberately equal to schema/version/count/path components."""
    aliases = Aliases(Path('.'))
    before = {
        'schema': SCHEMA, 'version': '1', 'count': 1, 'count_text': '1',
        'path': 'mac-private-zmx-diagnostic-1/result-private.json',
        'duration': '1 second with 1 millisecond', '1': 'numeric metadata key',
        'other_message': 'TimeoutError: owned process did not exit: 1',
        'synthetic_attempts': [{'value': {
            'pid': 1, 'ppid': 1, 'parent': 1, 'host_pid': 1, 'peer_pid': 1, 'uid': 1,
            'exit_codes': {'1': -15}, 'count': 1,
            'errors': ['TimeoutError: owned process did not exit: 1']}}],
        'fifteen_second_diagnostic': {'value': {'sample': {'stderr':
            'Sampling process 1 for 1 second with 1 millisecond of run time between samples\n'
            'Sample analysis of process 1 written to file mac-private-zmx-diagnostic-1/sample-private.txt\n'}}},
    }
    aliases.seed(before)
    expected = copy.deepcopy(before)
    identity = expected['synthetic_attempts'][0]['value']
    for key in Aliases.identity_keys:
        identity[key] = '<pid-001>'
    identity['uid'] = '<uid-001>'
    identity['exit_codes'] = {'<pid-001>': -15}
    identity['errors'] = ['TimeoutError: owned process did not exit: <pid-001>']
    expected['fifteen_second_diagnostic']['value']['sample']['stderr'] = (
        'Sampling process <pid-001> for 1 second with 1 millisecond of run time between samples\n'
        'Sample analysis of process <pid-001> written to file mac-private-zmx-diagnostic-1/sample-private.txt\n')
    actual = aliases.walk(before, path=('mac-admin-prerequisites.json',))
    checks.compare(expected, actual, 'PID 1 scoped substitutions', 'numeric_collision')
    checks.compare(before, aliases.reverse(actual), 'PID 1 reverse substitutions', 'numeric_collision')
    for message, path in [
        ('TimeoutError: owned process did not exit: 1\n',
         ('selected-log-projections.json', 'logs', '*', 'selected_lines', '*', 'text')),
        ('TimeoutError: owned process did not exit: 1',
         ('mac-admin-bundle-retry.json', 'retained_result_projection', 'value', 'reported_errors', '*')),
    ]:
        checks.check(aliases.walk(message, path=path) == message.replace(': 1', ': <pid-001>'),
                     'identified error PID aliased', 'numeric_collision')
    for label, value, key, path in [
        ('unbound exit-code PID', {'2': -15}, 'exit_codes', ()),
        ('unbound error PID', 'TimeoutError: owned process did not exit: 2', None,
         ('mac-admin-prerequisites.json', 'synthetic_attempts', '*', 'value', 'errors', '*')),
    ]:
        rejected = False
        try:
            aliases.walk(value, key=key, path=path)
        except ValueError:
            rejected = True
        checks.check(rejected, label, 'negative_controls')


def verify(root, public, check_manifest=True):
    checks = Checks()
    documents, aliases, index = make_documents(root, public, checks)
    actual = {name: json.loads((public / name).read_bytes()) for name in documents}
    validate_public_shape(actual, index, checks)
    check_numeric_collisions(checks)
    for name in documents:
        checks.compare(documents[name], actual[name], name)
    # Pure in-memory negative controls; no driver, child, runtime, compiler or file mutation.
    controls = [
        ('failure changed to pass', 'mac-admin-prerequisites.json', lambda d: d['synthetic_attempts'][0]['value'].__setitem__('passed', True)),
        ('administrative acceptance invented', 'mac-zmx-context.json', lambda d: d['comparison_result']['value'].__setitem__('administrative_stop_acceptance', True)),
        ('deadline relaxed', 'mac-zmx-context.json', lambda d: d['comparison_result']['value']['cases']['pristine'].__setitem__('observation_seconds', 4.0)),
        ('actual TERM removed', 'mac-admin-bundle-retry.json', lambda d: d['retry_result']['value'].__setitem__('recovery', [])),
        ('private alias changed', 'mac-admin-prerequisites.json', lambda d: d['synthetic_attempts'][0]['value'].__setitem__('stage', '<wrong-stage>')),
        ('selected log text changed', 'selected-log-projections.json', lambda d: d['logs'][0]['selected_lines'][0].__setitem__('text', 'success\n')),
    ]
    for label, name, change in controls:
        wrong = copy.deepcopy(actual[name])
        change(wrong)
        rejected = False
        try:
            Checks().compare(documents[name], wrong, 'negative')
        except ValueError:
            rejected = True
        checks.check(rejected, label, 'negative_controls')
    # These controls exercise semantic validators, not equality to regenerated JSON.
    shape_controls = []
    for name in documents:
        shape_controls.append(('aliased schema ' + name, name,
                               lambda d: d.__setitem__('schema', 'agterm-mac-admin-context-publication/<pid-004>')))
    shape_controls.append(('unresolved diagnostic result receipt', 'mac-admin-prerequisites.json',
        lambda d: d['fifteen_second_diagnostic']['original'].__setitem__(
            'path', 'mac-private-zmx-diagnostic-<pid-004>/result-private.json')))
    for stream in ['stdout.log', 'stderr.log', 'sample-private.txt']:
        def corrupt_log(d, stream=stream):
            row = next(row for row in d['logs'] if row['original']['path'] == DIAGNOSTIC + '/' + stream)
            row['original']['path'] = 'mac-private-zmx-diagnostic-<pid-004>/' + stream
        shape_controls.append(('unresolved diagnostic log receipt ' + stream,
                               'selected-log-projections.json', corrupt_log))
    shape_controls.append(('resolved receipt with wrong digest', 'mac-admin-prerequisites.json',
        lambda d: d['fifteen_second_diagnostic']['original'].__setitem__('sha256', '0' * 64)))
    for label, name, change in shape_controls:
        wrong = copy.deepcopy(actual)
        change(wrong[name])
        rejected = False
        try:
            validate_public_shape(wrong, index, Checks())
        except ValueError:
            rejected = True
        checks.check(rejected, label, 'negative_controls')
    if check_manifest:
        manifest = json.loads((public / 'manifest.json').read_bytes())
        checks.check(manifest['schema'] == 'agterm-mac-admin-context-manifest/1', 'manifest schema', 'schema')
        recorded = json.loads((public / 'verification.json').read_bytes())
        checks.check(recorded['schema'] == SCHEMA, 'recorded verification schema', 'schema')
        allowed = set(documents) | {'original-bindings.json', 'README.mac-admin-context-insertion.md',
                                   'verify-mac-admin-context.py', 'verification.json'}
        checks.check(set(manifest['files']) == allowed, 'complete public manifest', 'manifest')
        checks.check({path.name for path in public.iterdir()} == allowed | {'manifest.json'},
                     'no unpublished extra file or private alias map in public directory', 'manifest')
        for name, entry in manifest['files'].items():
            path = Path(name)
            checks.check(not path.is_absolute() and '..' not in path.parts, 'public manifest path', 'manifest')
            data = (public / path).read_bytes()
            checks.check(len(data) == entry['bytes'] and digest(data) == entry['sha256'], 'public manifest binding: ' + name, 'manifest')
    return {'schema': SCHEMA, 'passed': True, 'counts': dict(checks.counts),
            'original_files_rehashed': index['file_count'], 'original_bytes_rehashed': index['total_bytes'],
             'public_original_receipts_resolved': sum(RECEIPT_COUNTS.values()),
             'numeric_pid_aliases_scoped_to_identity_positions': True,
             'pid1_schema_version_path_count_controls_passed': True,
            'alias_categories': {kind: len(table) for kind, table in aliases.tables.items()},
            'same_account_not_independent_approval': True, 'no_new_runtime_or_compiler_execution': True,
            'historical_standalone_driver_receipt_missing': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root', type=Path, required=True)
    parser.add_argument('--public-root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(verify(args.original_root.resolve(), args.public_root.resolve()), indent=2))


if __name__ == '__main__':
    main()
