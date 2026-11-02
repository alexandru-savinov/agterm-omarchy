from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid

SOURCE = 'UNSELECTED_CANDIDATE'
SOURCE_RUN = 0
ASSETS = {
    'harness.sh': '9fe046d6fbbf0dbc79e0591b2fbe8869af4f79604d6a2f7a470c5750044cee40',
    'fixture.c': 'b96b70613a14e562eb5d2edec02653b2a41221eedaaf06880334eda3b15ca470',
    'validator.py': 'cd32706bc7454324cc092345e506385bc1e9aa9fa852d0417a1893f3f0fc24b6',
    'config-probe.py': 'c521ff81ffa6677eeb61b1601c5535cda140ee605682449d6abb63d683d6eed9',
}
TOKEN_KEY = 'OMARCHY_NATIVE_RUN_TOKEN'
RUNTIME_FILES = ('agterm-ghostty-runtime.conf', 'agterm-ghostty-defaults.conf',
                 'agterm-ghostty-empty-light-theme', 'agterm-ghostty-empty-dark-theme')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name + '.',
                                     delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    result = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
    if stat.S_ISLNK(info.st_mode):
        result.update(kind='symlink', target=os.readlink(path))
        if path.is_file():
            result['target_sha256'] = digest(path)
    elif stat.S_ISREG(info.st_mode):
        result.update(kind='file', sha256=digest(path))
    elif stat.S_ISDIR(info.st_mode):
        result['kind'] = 'directory'
    else:
        result['kind'] = 'special'
    return result


def manifest(root):
    root = Path(root)
    result = {'.': fingerprint(root)}
    if root.is_dir():
        result.update((str(path.relative_to(root)), fingerprint(path)) for path in sorted(root.rglob('*')))
    return result


def process_record(pid, token=None):
    entry = Path('/proc') / str(pid)
    try:
        raw = (entry / 'stat').read_text()
        end = raw.rindex(')')
        fields = raw[end + 2:].split()
        row = dict(pid=pid, name=raw[raw.index('(') + 1:end], state=fields[0], parent=int(fields[1]),
                   start=fields[19], uid=entry.stat().st_uid)
        if token is not None and row['uid'] == os.getuid():
            try:
                environment = dict(item.split(b'=', 1) for item in (entry / 'environ').read_bytes().split(b'\0')
                                   if b'=' in item)
                row['tagged'] = environment.get(TOKEN_KEY.encode()) == token.encode()
            except (OSError, ValueError):
                row['tagged'] = False
        return row
    except (OSError, ValueError, IndexError):
        return None


def processes(token=None):
    result = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            row = process_record(int(entry.name), token)
            if row is not None:
                result[row['pid']] = row
    return result


def key(row):
    return row['pid'], row['start']


def alive(row):
    return row is not None and row['state'] not in ('Z', 'X', 'x')


def app_process(row):
    name = row['name'].lower()
    return 'agterm' in name or name.startswith('zmx')


def protected_tree(snapshot, extra=()):
    roots = {pid for pid, row in snapshot.items() if alive(row) and app_process(row)} | set(extra)
    require(all(pid in snapshot and alive(snapshot[pid]) for pid in roots), 'Missing protected process root')
    members = set(roots)
    while True:
        expanded = members | {pid for pid, row in snapshot.items() if row['parent'] in members and alive(row)}
        if expanded == members:
            break
        members = expanded
    return {pid: snapshot[pid] for pid in members}


def preservation(protected, current):
    lost = {}
    for pid, before in protected.items():
        after = current.get(pid)
        if after is None:
            lost[str(pid)] = 'missing'
        elif after['start'] != before['start']:
            lost[str(pid)] = 'PID reused'
        elif not alive(after):
            lost[str(pid)] = 'not live: ' + after['state']
    return {'passed': not lost, 'protected_process_count': len(protected), 'lost': lost}


class OwnedProcesses:
    def __init__(self, baseline, uid):
        self.baseline = {key(row) for row in baseline.values()}
        self.uid = uid
        self.records = {}

    def add(self, row):
        if row and row['uid'] == self.uid and key(row) not in self.baseline:
            self.records.setdefault(key(row), dict(row))

    def observe(self, current):
        for row in current.values():
            if row.get('tagged'):
                self.add(row)
        while True:
            parents = {pid for pid, start in self.records
                       if pid in current and current[pid]['start'] == start and alive(current[pid])}
            old_count = len(self.records)
            for row in current.values():
                if row['parent'] in parents:
                    self.add(row)
            if old_count == len(self.records):
                break

    def remaining(self, current):
        return [row for row in current.values() if key(row) in self.records and alive(row)]


def signal_owned(row, sig):
    """pidfd closes the race between checking start-time and signalling a recycled numeric PID."""
    require(row['uid'] == os.getuid(), 'Refusing a different-UID process')
    try:
        descriptor = os.pidfd_open(row['pid'])
    except ProcessLookupError:
        return False
    try:
        current = process_record(row['pid'])
        if not alive(current) or key(current) != key(row) or current['uid'] != row['uid']:
            return False
        signal.pidfd_send_signal(descriptor, sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(descriptor)


def endpoint_for(state):
    value = 14695981039346656037
    for byte in str(Path(state).resolve()).encode():
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return '/tmp/agterm-zmx-' + format(value, '016x')


LAUNCHER = r'''#!/usr/bin/python3
import json
import os
from pathlib import Path
import re
import stat
import sys

settings = SETTINGS

def need(ok, message):
    if not ok:
        raise RuntimeError(message)

need(os.environ.get('OMARCHY_NATIVE_RUN_TOKEN') == settings['token'], 'Missing operator ownership token')
state = Path(os.environ['AGTERM_STATE_DIR'])
run = state.parent
need(state.name == 'state' and run.parent == Path('/tmp') and re.fullmatch(r'agt-wl-[A-Za-z0-9_-]+', run.name),
     'Unexpected harness state path')
need(run.resolve() == run and state.resolve() == state and run.stat().st_uid == os.getuid()
     and stat.S_IMODE(run.stat().st_mode) == 0o700, 'Harness state is not private')
need(os.environ['AGTERM_CONTROL_SOCKET'] == str(state / 'agterm.sock'), 'Wrong harness socket')
value = 14695981039346656037
for byte in str(state).encode():
    value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
endpoint = Path('/tmp/agterm-zmx-' + format(value, '016x'))
records = Path(settings['records'])
prior = [json.loads(path.read_text()) for path in records.glob('*.json')]
need(all(row['state'] == str(state) and row['token'] == settings['token'] for row in prior),
     'State changed between launches')
need(prior or not os.path.lexists(endpoint), 'Endpoint existed before this test')
if os.path.lexists(endpoint):
    need(not endpoint.is_symlink() and endpoint.is_dir() and endpoint.stat().st_uid == os.getuid(),
         'Endpoint is not an owned directory')
runtime = Path(settings['runtime'])
need(runtime.resolve() == runtime and runtime.is_dir() and runtime.stat().st_uid == os.getuid()
     and stat.S_IMODE(runtime.stat().st_mode) == 0o700, 'Runtime is not private')
os.environ['XDG_RUNTIME_DIR'] = str(runtime)
os.environ['WAYLAND_DISPLAY'] = settings['wayland']
raw = Path('/proc/self/stat').read_text()
fields = raw[raw.rindex(')') + 2:].split()
process = dict(pid=os.getpid(), start=fields[19], uid=os.getuid(), name='test-launcher',
               state=fields[0], parent=int(fields[1]))
record = dict(process=process, state=str(state), endpoint=str(endpoint), token=settings['token'],
              runtime=str(runtime), wayland=settings['wayland'])
with (records / (str(process['pid']) + '-' + process['start'] + '.json')).open('x') as handle:
    json.dump(record, handle)
    handle.flush()
    os.fsync(handle.fileno())
os.execv(settings['app'], [settings['app'], *sys.argv[1:]])
'''


def make_launcher(stage, mode, payload, desktop, token):
    runtime = stage / (mode + '-runtime')
    records = stage / (mode + '-launches')
    runtime.mkdir(mode=0o700)
    records.mkdir(mode=0o700)
    wayland = Path(desktop['WAYLAND_DISPLAY'])
    if not wayland.is_absolute():
        wayland = Path(desktop['XDG_RUNTIME_DIR']) / wayland
    wayland = wayland.resolve()
    require(wayland.is_socket(), 'Desktop Wayland endpoint is not a socket')
    settings = dict(runtime=str(runtime), records=str(records), wayland=str(wayland), token=token,
                    app=str(payload / 'bin/agterm-linux'))
    launcher = stage / (mode + '-launch-app.py')
    with launcher.open('x') as handle:
        handle.write(LAUNCHER.replace('SETTINGS', repr(settings), 1))
    launcher.chmod(0o700)
    return launcher, runtime, records


class Interrupts:
    def __init__(self):
        self.received = []
        self.cleaning = False
        self.previous = {}

    def enter(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.previous[sig] = signal.signal(sig, self.handle)

    def handle(self, sig, frame):
        self.received.append(sig)
        if not self.cleaning:
            raise InterruptedError('Operator interrupted by signal ' + str(sig))

    def restore(self):
        for sig, previous in self.previous.items():
            signal.signal(sig, previous)


class ModeRun:
    def __init__(self, stage, mode, payload, base, protected, interrupts):
        self.stage, self.mode, self.payload = stage, mode, payload
        self.base, self.protected, self.interrupts = base, protected, interrupts
        self.dest = stage / mode
        self.token = uuid.uuid4().hex
        self.baseline = processes()
        self.owned = OwnedProcesses(self.baseline, os.getuid())
        self.process = None
        self.leader = None
        self.states, self.endpoints, self.shells = set(), set(), {}
        self.actions, self.errors = [], []
        self.timed_out = False
        self.forced = False
        self.launcher, self.runtime, self.records = make_launcher(stage, mode, payload, base, self.token)
        self.result = {'passed': False, 'started_at_utc': stamp(), 'launcher_sha256': digest(self.launcher),
                       'private_runtime_directory': self.runtime.name}

    def journal(self):
        atomic_json(self.stage / (self.mode + '-recovery.json'), {
            'token': self.token, 'mode': self.mode, 'recorded_at_utc': stamp(),
            'processes': list(self.owned.records.values()), 'states': sorted(self.states),
            'endpoints': sorted(self.endpoints), 'shell_pids': self.shells,
            'protected_processes': list(self.protected.values()),
            'cleanup_actions': self.actions, 'errors': self.errors})

    def collect(self):
        current = processes(self.token)
        self.owned.observe(current)
        for path in self.records.glob('*.json'):
            try:
                record = json.loads(path.read_text())
            except (ValueError, OSError):
                continue  # the launcher may still be writing; a missing complete record cannot pass
            require(record['token'] == self.token and record['runtime'] == str(self.runtime),
                    'Launch record identity mismatch')
            state = Path(record['state'])
            require(state.name == 'state' and state.parent.parent == Path('/tmp')
                    and re.fullmatch(r'agt-wl-[A-Za-z0-9_-]+', state.parent.name), 'Unsafe launch state record')
            require(record['endpoint'] == endpoint_for(state), 'Launch endpoint/state mismatch')
            self.owned.add(record['process'])
            self.states.add(str(state))
            self.endpoints.add(record['endpoint'])
            for marker in ('shell-pid-before', 'shell-pid-after'):
                path = state.parent / marker
                if path.is_file():
                    value = path.read_text().strip()
                    require(value.isdigit(), 'Invalid shell PID marker')
                    self.shells[marker] = int(value)
        self.journal()
        return current

    def safe_collect(self):
        try:
            return self.collect()
        except BaseException as error:
            self.errors.append('collect: ' + type(error).__name__ + ': ' + str(error))
            current = processes(self.token)
            self.owned.observe(current)
            return current

    def send(self, row, sig):
        require(key(row) in self.owned.records and key(row) not in self.owned.baseline,
                'Refusing an unowned or pre-existing process')
        try:
            sent = signal_owned(row, sig)
            self.actions.append({'pid': row['pid'], 'start': row['start'], 'signal': sig, 'sent': sent})
        except OSError as error:
            self.errors.append('signal: ' + type(error).__name__ + ': ' + str(error))

    def wait_until(self, seconds, leader_only=False):
        deadline = time.monotonic() + seconds
        while True:
            if self.process is not None:
                self.process.poll()
            current = self.safe_collect()
            pending = (self.process is not None and self.process.returncode is None) if leader_only else self.owned.remaining(current)
            if not pending or time.monotonic() >= deadline:
                return current
            time.sleep(0.2)

    def endpoint_cleanup(self):
        """Only transcript-claimed daemon names at the state-derived, initially absent endpoint."""
        claimed = set()
        transcript = self.dest / 'control.jsonl'
        for line in transcript.read_text().splitlines() if transcript.exists() else ():
            try:
                row = json.loads(line)
                if row['args'] != ['zmx', 'list']:
                    continue
                inventory = row['response']['result']['zmx']
                endpoint = inventory['endpoint']
                require(endpoint['socketDirectory'] in self.endpoints
                        and endpoint['executable'] == str(self.payload / 'bin/zmx'), 'Unowned zmx endpoint')
                for item in inventory['entries']:
                    if item['state'] == 'claimed':
                        require(re.fullmatch(r'agterm-[0-9a-f]{32}', item['daemon']), 'Unexpected daemon name')
                        claimed.add((endpoint['socketDirectory'], item['daemon']))
            except (ValueError, KeyError):
                continue
        deadline = time.monotonic() + 15
        for endpoint, name in sorted(claimed):
            path = Path(endpoint)
            if not path.exists():
                continue
            require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid(),
                    'Endpoint ownership changed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.errors.append('Endpoint cleanup deadline reached')
                break
            env = dict(self.base, ZMX_DIR=endpoint, **{TOKEN_KEY: self.token})
            command = subprocess.run([str(self.payload / 'bin/zmx'), 'kill', name, '--force'], env=env,
                                     capture_output=True, text=True, timeout=min(3, remaining),
                                     stdin=subprocess.DEVNULL)
            self.actions.append({'endpoint': endpoint, 'daemon': name, 'returncode': command.returncode,
                                 'confirmed': 'killed session ' + name in (command.stdout + command.stderr).splitlines()})

    def finish(self, failed):
        was_cleaning = self.interrupts.cleaning
        self.interrupts.cleaning = True
        try:
            current = self.safe_collect()
            if self.process is not None and self.process.poll() is None:
                if self.leader is not None:
                    self.send(self.leader, signal.SIGINT)
                current = self.wait_until(45, leader_only=True)
            else:
                current = self.wait_until(2)
            if self.owned.remaining(current):
                self.forced = True
                # Stop the harness before endpoint cleanup so it cannot restart a frontend.
                if self.leader is not None:
                    self.send(self.leader, signal.SIGTERM)
                    self.wait_until(3, leader_only=True)
                    self.send(self.leader, signal.SIGKILL)
                try:
                    self.endpoint_cleanup()
                except BaseException as error:
                    self.errors.append('endpoint cleanup: ' + type(error).__name__ + ': ' + str(error))
                for sig, seconds in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                    current = self.safe_collect()
                    for row in self.owned.remaining(current):
                        self.send(row, sig)
                    current = self.wait_until(seconds)
            else:
                current = self.wait_until(1)
            remaining = self.owned.remaining(current)
            new_apps = [row for row in current.values() if alive(row) and app_process(row)
                        and key(row) not in self.owned.baseline]
            retained = [str(Path(state).parent) for state in self.states if Path(state).parent.exists()]
            protected = preservation(self.protected, current)
            audit = {'passed': not remaining and not new_apps and not retained and protected['passed']
                     and not self.timed_out and not self.forced and not self.errors,
                     'tracked_process_count': len(self.owned.records), 'remaining_processes': remaining,
                     'new_app_processes': new_apps, 'retained_recovery_paths': retained,
                     'endpoints': sorted(self.endpoints), 'preservation': protected,
                     'timed_out': self.timed_out, 'forced_cleanup': self.forced, 'shell_pids': self.shells,
                     'cleanup_actions': self.actions, 'errors': self.errors}
            self.result.update(returncode=None if self.process is None else self.process.poll(),
                               independent_cleanup=audit, ended_at_utc=stamp())
            self.journal()
        finally:
            self.interrupts.cleaning = was_cleaning

    def execute(self):
        failed = True
        try:
            require(not os.path.lexists(self.dest), 'Refusing reused mode artifacts')
            env = dict(self.base, AGTERM_TEST_RESTORE_MODE=self.mode, AGTERM_OMARCHY_ARTIFACTS=str(self.dest),
                       AGTERM_TEST_BIN=str(self.launcher), **{TOKEN_KEY: self.token})
            if self.mode == 'live':
                env['LD_PRELOAD'] = str(self.stage / 'fixture.so')
            self.journal()  # survives even a later uncatchable operator termination
            with (self.stage / (self.mode + '.log')).open('x') as output:
                self.process = subprocess.Popen(['bash', str(self.stage / 'harness.sh')], env=env,
                                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                self.leader = process_record(self.process.pid)
                self.owned.add(self.leader)
                deadline = time.monotonic() + 240
                while self.process.poll() is None:
                    self.collect()
                    if time.monotonic() >= deadline:
                        self.timed_out = True
                        raise TimeoutError('Native harness exceeded 240 seconds')
                    time.sleep(0.2)
            failed = self.process.returncode != 0
        except BaseException as error:
            self.result['error'] = type(error).__name__ + ': ' + str(error)
        finally:
            try:
                self.finish(failed)
            except BaseException as error:
                self.result['cleanup_error'] = type(error).__name__ + ': ' + str(error)
                self.result['passed'] = False
                # Independent final preservation is also run by main even if this audit fails.
                try:
                    self.journal()
                except BaseException as journal_error:
                    self.result['journal_error'] = type(journal_error).__name__ + ': ' + str(journal_error)
        for name in ('result', 'cleanup'):
            try:
                path = self.dest / (name + '.json')
                self.result[name] = json.loads(path.read_text()) if path.exists() else {}
            except BaseException as error:
                self.result[name] = {}
                self.result['error'] = 'Invalid ' + name + ': ' + str(error)
        return self.result


def accept_mode(record, mode, app_sha, zmx_sha):
    require(not record.get('error') and not record.get('cleanup_error'), 'Mode execution or audit failed')
    result, cleanup, audit = record['result'], record['cleanup'], record['independent_cleanup']
    require(record['returncode'] == 0 and result.get('passed') and cleanup.get('passed') and audit['passed'],
            'Harness/cleanup/preservation failed')
    require(result['restore_mode'] == mode and len(result['checks']) == (9 if mode == 'live' else 7),
            'Wrong mode or incomplete check count')
    for identity in (result['identity'], cleanup['identity']):
        require(identity['harness']['sha256'] == ASSETS['harness.sh'], 'Wrong harness identity')
    require(result['identity']['run_id'] == cleanup['identity']['run_id'], 'Result/cleanup run mismatch')
    apps = result['identity']['app_processes']
    require(len(apps) == (2 if mode == 'live' else 1) and all(row['sha256'] == app_sha for row in apps),
            'Wrong running app identity or launch count')
    require(audit['tracked_process_count'] > 0 and len(audit['endpoints']) == 1, 'Incomplete independent run inventory')
    if mode == 'live':
        shells = audit['shell_pids']
        require(set(shells) == {'shell-pid-before', 'shell-pid-after'} and len(set(shells.values())) == 1,
                'Live shell PID evidence incomplete or unequal')
        require(not Path('/proc/' + str(next(iter(shells.values())))).exists(), 'Live shell PID still exists')
        require(cleanup['identity']['positive_control']['passed'], 'Missing sentinel cleanup proof')
        require(result['identity']['binaries']['zmx']['sha256'] == zmx_sha, 'Wrong running zmx')
    record['passed'] = True


def run_config_probe(stage, payload, base, protected, app_sha, interrupts=None):
    """Run the additional probe in an owned child, retaining an independent process audit."""
    result = {'passed': False, 'started_at_utc': stamp(), 'actions': [], 'errors': []}
    token = uuid.uuid4().hex
    owned = OwnedProcesses(processes(), os.getuid())
    process = None
    claimed = False
    images = {}
    probe_output = stage / 'config-probe'
    report_path = stage / 'config-probe-result.json'
    launcher = stage / 'config-probe-launch.py'
    journal = stage / 'config-probe-recovery.json'
    attempt = stage / 'config-probe-attempt.json'
    settings = dict(script=str(stage / 'config-probe.py'), payload=str(payload),
                    output=str(probe_output), result=str(report_path), app_sha=app_sha)
    bootstrap = '''import importlib.util, json, os, signal
from pathlib import Path
settings = SETTINGS
def interrupted(sig, frame):
    raise InterruptedError('Operator probe interrupted: ' + str(sig))
for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupted)
spec = importlib.util.spec_from_file_location('config_probe', settings['script'])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.run_probe(Path(settings['payload']), Path(settings['output']), dict(os.environ), settings['app_sha'])
with Path(settings['result']).open('x') as stream:
    json.dump(result, stream)
    stream.flush()
    os.fsync(stream.fileno())
raise SystemExit(0 if result.get('passed') is True else 1)
'''

    def collect(*, recovery=False):
        current = processes(token)
        owned.observe(current)
        for row in owned.remaining(current):
            if key(row) not in images:
                try:
                    executable = Path('/proc') / str(row['pid']) / 'exe'
                    if os.readlink(executable) != str(payload / 'bin/agterm-linux.bin'):
                        continue
                    value = digest(executable)
                    after = process_record(row['pid'])
                    if after and key(after) == key(row):
                        images[key(row)] = dict(process=row, sha256=value)
                except OSError:
                    pass  # A missing live image cannot satisfy acceptance below.
        try:
            atomic_json(journal, dict(token=token, processes=list(owned.records.values()),
                                     protected_processes=list(protected.values()), actions=result['actions']))
        except BaseException as error:
            message = 'Probe recovery journal: ' + type(error).__name__ + ': ' + str(error)
            if message not in result['errors']:
                result['errors'].append(message)
            if not recovery:
                raise
        return current

    def wait_until(seconds, *, recovery=False):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            remaining = owned.remaining(collect(recovery=recovery))
            if process is not None:
                process.poll()
            if not remaining:
                return []
            time.sleep(0.2)
        return owned.remaining(collect(recovery=recovery))

    try:
        require(not any(os.path.lexists(path) for path in
                        (probe_output, report_path, launcher, journal, attempt,
                         stage / 'config-probe.log', stage / 'config-probe-operator.json')),
                'Refusing a previously used config-probe attempt')
        require(digest(stage / 'config-probe.py') == ASSETS['config-probe.py'], 'Wrong config probe bytes')
        with attempt.open('x') as stream:
            json.dump(dict(token=token, started_at_utc=stamp()), stream)
            stream.flush()
            os.fsync(stream.fileno())
        claimed = True
        with launcher.open('x') as stream:
            stream.write(bootstrap.replace('SETTINGS', repr(settings), 1))
        result['launcher_sha256'] = digest(launcher)
        result['probe_sha256'] = ASSETS['config-probe.py']
        collect()
        with (stage / 'config-probe.log').open('x') as output:
            process = subprocess.Popen(['/usr/bin/python3', str(launcher)],
                                       env=dict(base, **{TOKEN_KEY: token}),
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(process_record(process.pid))
            deadline = time.monotonic() + 360
            while process.poll() is None:
                collect()
                require(time.monotonic() < deadline, 'Config probe exceeded 360 seconds')
                time.sleep(0.2)
        result['returncode'] = process.returncode
        result['probe'] = json.loads(checked_file(report_path, os.getuid()).read_text())
        require(process.returncode == 0 and result['probe'].get('passed') is True, 'Config probe failed')
        require(not wait_until(5), 'Config probe left owned processes alive')
        require(len(images) == 2 and all(image['sha256'] == app_sha for image in images.values()),
                'Config probe running app identities are incomplete or wrong')
        result['passed'] = True
    except BaseException as error:
        result['errors'].append(type(error).__name__ + ': ' + str(error))
    finally:
        if claimed:
            was_cleaning = interrupts.cleaning if interrupts is not None else False
            if interrupts is not None:
                interrupts.cleaning = True
            # Every recovery signal is explicit, identity-checked, recorded and disqualifies acceptance.
            # The probe owns its private file cleanup; do not guess paths or remove a real runtime entry.
            try:
                for sig, seconds in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
                    remaining = owned.remaining(collect(recovery=True))
                    for row in remaining:
                        if signal_owned(row, sig):
                            result['actions'].append(dict(pid=row['pid'], start=row['start'], signal=int(sig)))
                    if not remaining or not wait_until(seconds, recovery=True):
                        break
                current = collect(recovery=True)
                result['remaining_owned_processes'] = owned.remaining(current)
                result['preservation'] = preservation(protected, current)
                result['tracked_process_count'] = len(owned.records)
                result['app_processes'] = list(images.values())
                if process is not None:
                    process.poll()
                result['passed'] = bool(result['passed'] and not result['actions'] and not result['errors']
                                        and not result['remaining_owned_processes'] and result['preservation']['passed'])
            except BaseException as error:
                result['passed'] = False
                result['errors'].append('Independent probe cleanup: ' + type(error).__name__ + ': ' + str(error))
            finally:
                if interrupts is not None:
                    interrupts.cleaning = was_cleaning
            result['ended_at_utc'] = stamp()
            try:
                atomic_json(stage / 'config-probe-operator.json', result)
            except BaseException as error:
                result['passed'] = False
                result['errors'].append('Probe operator report: ' + type(error).__name__ + ': ' + str(error))
        else:
            result['ended_at_utc'] = stamp()

    return result


def claim_stage(stage, parent, uid):
    require(stage.parent == parent and stage.name.startswith('native-config-isolation.'), 'Unexpected stage path')
    require(stage.resolve() == stage and stage.is_dir() and stage.stat().st_uid == uid
            and stat.S_IMODE(stage.stat().st_mode) == 0o700, 'Stage is not a private owned directory')
    require(not os.path.lexists(stage / 'agterm-stage') and not os.path.lexists(stage / 'report.json'),
            'Refusing a previously extracted or reported attempt')
    with (stage / 'run-attempt.json').open('x') as handle:
        json.dump({'started_at_utc': stamp(), 'source_commit': SOURCE, 'source_run': SOURCE_RUN}, handle)
        handle.flush()
        os.fsync(handle.fileno())


def checked_file(path, uid):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == uid, 'Unsafe input file: ' + path.name)
    return path


def main(config):
    report = {'passed': False, 'source_commit': SOURCE, 'source_run': SOURCE_RUN,
              'started_at_utc': stamp(), 'modes': {}, 'phase': 'preflight'}
    stage = None
    snapshots = {}
    protected = None
    interrupts = Interrupts()
    interrupts.enter()
    try:
        require(config['source_commit'] == SOURCE and config['source_run'] == SOURCE_RUN, 'Wrong source or run')
        require(os.uname().sysname == 'Linux' and os.uname().machine in ('aarch64', 'arm64'),
                'Operator helper requires the intended Linux ARM64 runtime')
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Safe Linux pidfd API unavailable')
        account = pwd.getpwnam('alexandru')
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        os.environ.update(HOME=account.pw_dir, USER=account.pw_name, LOGNAME=account.pw_name,
                          XDG_RUNTIME_DIR=f'/run/user/{account.pw_uid}',
                          DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{account.pw_uid}/bus',
                          PATH='/usr/local/bin:/usr/bin:/bin')
        os.chdir(account.pw_dir)
        require(account.pw_shell == '/bin/bash', 'Unexpected actual login shell')
        descriptor = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(descriptor, 0)
        finally:
            os.close(descriptor)
        requested = Path(config['stage'])
        claim_stage(requested, Path(account.pw_dir) / 'src/agterm-logs', os.getuid())
        stage = requested
        report['phase'] = 'validate'
        atomic_json(stage / 'report.json', report)
        protected = protected_tree(processes(), config.get('protected_pids', ()))
        atomic_json(stage / 'protected-processes.json', list(protected.values()))
        installed = Path(account.pw_dir) / '.local/lib/agterm'
        snapshots['installed_payload_unchanged'] = (installed, manifest(installed))
        preparation = json.loads(checked_file(stage / 'preparation.json', os.getuid()).read_text())
        require(preparation['source_commit'] == SOURCE and preparation['source_run'] == SOURCE_RUN, 'Wrong preparation')
        require(set(preparation['assets']) == set(ASSETS), 'Wrong prepared asset set')
        for name, expected in ASSETS.items():
            require(preparation['assets'][name]['sha256'] == expected
                    and digest(checked_file(stage / name, os.getuid())) == expected, 'Wrong prepared ' + name)
        fixture_sha = digest(checked_file(stage / 'fixture.so', os.getuid()))
        require(fixture_sha == preparation['fixture_binary_sha256'], 'Fixture binary mismatch')
        archive = checked_file(stage / 'candidate.tar.gz', os.getuid())
        require(digest(archive) == config['archive_sha256'], 'Archive digest mismatch')
        spec = importlib.util.spec_from_file_location('candidate_validator', stage / 'validator.py')
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        validator.validate_payload(archive.read_bytes(), 'aarch64', SOURCE)
        payload = stage / 'agterm-stage'
        with tarfile.open(archive, 'r:gz') as bundle:
            bundle.extractall(stage, filter='data')
        require((payload / 'share/agterm/COMMIT').read_text().strip() == SOURCE, 'Extracted source mismatch')
        before = manifest(payload)
        snapshots['payload_unchanged'] = (payload, before)
        require(digest(payload / 'bin/zmx') == config['zmx_sha256'], 'Extracted zmx mismatch')
        atomic_json(stage / 'payload-before.json', before)
        base = {k: v for k, v in os.environ.items() if not k.startswith(('AGTERM_', 'ZMX_', 'GHOSTTY_', 'LD_'))
                and k not in ('ZDOTDIR', TOKEN_KEY)}
        manager = subprocess.check_output(['systemctl', '--user', 'show-environment'], text=True, timeout=10)
        session_keys = {'WAYLAND_DISPLAY', 'DISPLAY', 'XDG_RUNTIME_DIR', 'HYPRLAND_INSTANCE_SIGNATURE', 'DBUS_SESSION_BUS_ADDRESS'}
        desktop = {}
        for line in manager.splitlines():
            name, _, value = line.partition('=')
            if name in session_keys:
                require(value and not value.startswith(("'", '"', '$')), 'Unparsed desktop environment value')
                desktop[name] = value
        require(session_keys <= desktop.keys(), 'Incomplete desktop environment')
        real_runtime = Path(desktop['XDG_RUNTIME_DIR'])
        require(real_runtime == Path(f'/run/user/{account.pw_uid}') and real_runtime.resolve() == real_runtime
                and real_runtime.stat().st_uid == os.getuid(), 'Unexpected desktop runtime directory')
        runtime_before = {name: fingerprint(real_runtime / name) for name in RUNTIME_FILES}
        base.update(desktop)
        base.update(AGTERM_TEST_CTL=str(payload / 'bin/agtermctl'),
                    AGTERM_GHOSTTY_RESOURCES=str(payload / 'share/ghostty'),
                    AGTERM_RESOURCE_ROOT=str(payload / 'share/agterm'), AGTERM_ZMX_PATH=str(payload / 'bin/zmx'),
                    LIBGL_ALWAYS_SOFTWARE='1')
        report.update(archive_sha256=config['archive_sha256'], zmx_sha256=config['zmx_sha256'],
                      harness_sha256=ASSETS['harness.sh'], software_rendering=True, real_login_shell=account.pw_shell,
                      live_fixture_source_sha256=ASSETS['fixture.c'], live_fixture_binary_sha256=fixture_sha)
        for name in ('agterm-linux.bin', 'agtermctl.bin'):
            result = subprocess.run(['ldd', str(payload / 'bin' / name)],
                                    env=dict(base, LD_LIBRARY_PATH=str(payload / 'lib')),
                                    text=True, capture_output=True, timeout=20)
            (stage / (name + '.ldd.txt')).write_text(result.stdout + result.stderr)
            require(result.returncode == 0 and 'not found' not in result.stdout + result.stderr, 'Missing library: ' + name)
        probe = Path(tempfile.mkdtemp(prefix='zmx-probe.', dir=stage))
        direct = subprocess.run([str(payload / 'bin/zmx'), 'list'], env=dict(base, ZMX_DIR=str(probe)),
                                text=True, capture_output=True, timeout=10)
        require(direct.returncode == 0 and not direct.stdout.strip(), 'Bundled zmx direct CPU probe failed')
        for mode in ('none', 'live'):
            report['phase'] = mode
            run = ModeRun(stage, mode, payload, base, protected, interrupts)
            record = report['modes'][mode] = run.result
            atomic_json(stage / 'report.json', report)
            run.execute()
            record['shared_runtime_unchanged'] = runtime_before == {name: fingerprint(real_runtime / name) for name in RUNTIME_FILES}
            record['installed_payload_unchanged'] = snapshots['installed_payload_unchanged'][1] == manifest(installed)
            record['login_shell_unchanged'] = pwd.getpwuid(os.getuid()).pw_shell == account.pw_shell
            atomic_json(stage / 'report.json', report)
            require(record['shared_runtime_unchanged'] and record['installed_payload_unchanged'] and record['login_shell_unchanged'],
                    'Owner runtime, payload, or login shell changed')
            accept_mode(record, mode, before['bin/agterm-linux.bin']['sha256'], config['zmx_sha256'])
            atomic_json(stage / 'report.json', report)
        report['phase'] = 'config-probe'
        atomic_json(stage / 'report.json', report)
        report['config_probe'] = run_config_probe(stage, payload, base, protected,
                                                 before['bin/agterm-linux.bin']['sha256'], interrupts)
        atomic_json(stage / 'report.json', report)
        require(report['config_probe']['passed'], 'Additional config/lifecycle probe failed')
        report['passed'] = True
    except BaseException as error:
        report.update(passed=False, error=type(error).__name__ + ': ' + str(error))
    finally:
        interrupts.cleaning = True
        checks = {}
        for name, (path, before) in snapshots.items():
            try:
                checks[name] = before == manifest(path)
            except BaseException as error:
                checks[name] = False
                report[name + '_error'] = type(error).__name__ + ': ' + str(error)
        if protected is not None:
            try:
                report['preservation'] = preservation(protected, processes())
                checks['preexisting_sessions_preserved'] = report['preservation']['passed']
            except BaseException as error:
                checks['preexisting_sessions_preserved'] = False
                report['preservation_error'] = type(error).__name__ + ': ' + str(error)
        for name, check in (
                ('login_shell_unchanged', lambda: pwd.getpwuid(os.getuid()).pw_shell == account.pw_shell),
                ('shared_runtime_unchanged', lambda: runtime_before == {n: fingerprint(real_runtime / n) for n in RUNTIME_FILES})):
            try:
                checks[name] = check()
            except NameError:
                checks[name] = None  # preflight did not reach the relevant snapshot
            except BaseException as error:
                checks[name] = False
                report[name + '_error'] = type(error).__name__ + ': ' + str(error)
        report.update(checks)
        report.update(ended_at_utc=stamp(), signals=interrupts.received)
        report['passed'] = bool(report['passed'] and all(value is True for value in checks.values())
                                and not interrupts.received and set(report['modes']) == {'none', 'live'}
                                and report.get('config_probe', {}).get('passed') is True)
        report['phase'] = 'complete' if report['passed'] else 'failed'
        if stage is not None:
            try:
                atomic_json(stage / 'report.json', report)
            except BaseException as error:
                report.update(passed=False, report_write_error=type(error).__name__ + ': ' + str(error))
        print(json.dumps({'stage': str(stage) if stage is not None else None, 'report': report}), flush=True)
        interrupts.restore()
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main(CONFIG))
