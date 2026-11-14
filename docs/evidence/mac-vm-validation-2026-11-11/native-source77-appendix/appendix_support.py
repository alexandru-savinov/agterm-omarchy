"""File-only comparison and redaction helpers, copied from the frozen prior verifier.
Original SHA256: aec138e120316c010822cd68242f0003e4eeebba6fbff278a446e15fa37ff5f1
Home-regex prefixes are spelled as concatenated strings so source scanning does
not mistake the regex templates themselves for personal directory names.
No original module is imported or executed.
"""
import collections
import hashlib
import json
import re

PID_KEYS = {"pid", "parent", "supervisor_pid", "build_pid", "shell-pid-before", "shell-pid-after"}

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
            ('private-stage', '/' + 'home' + r'/[^/\s\"\x27]+/src/agterm-logs/[^/\s\"\x27\)\]:,}]+'),
            ('guest-home', '/' + 'home' + r'/[^/\s\"\x27\)\]:,}]+'),
            ('host-home', '/' + 'Users' + r'/[^/\s\"\x27\)\]:,}]+'),
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

