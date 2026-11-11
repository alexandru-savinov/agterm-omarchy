#!/usr/bin/env python3
"""Root-invoked Linux probe; import run_probe after validating the candidate artifact.

The ordinary/Live pixel harness remains a separate gate. This probe checks two mapped,
realized instances, PTY/control activity, bounded config files and graceful shutdown.
It never changes desktop appearance. Output directories are exclusive, retained evidence;
only the private short socket directory is removed. Forced recovery always fails the run.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import signal
import stat
import subprocess
import tempfile
import time
import uuid

TOKEN = 'OMARCHY_CONFIG_PROBE_TOKEN'
LIMITS = {'total': 180, 'command': 10, 'ready': 20, 'shutdown': 10, 'drain': 2, 'recovery': 5}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def record(pid, token=None):
    try:
        entry = Path('/proc') / str(pid)
        raw = (entry / 'stat').read_text()
        fields = raw[raw.rindex(')') + 2:].split()
        item = dict(pid=pid, start=fields[19], parent=int(fields[1]), state=fields[0], uid=entry.stat().st_uid)
        if token and item['uid'] == os.getuid():
            try:
                item['tagged'] = (TOKEN + '=' + token).encode() in (entry / 'environ').read_bytes().split(b'\0')
            except OSError:
                item['tagged'] = False
        return item
    except (OSError, ValueError, IndexError):
        return None


def snapshot(token=None):
    return {int(p.name): row for p in Path('/proc').iterdir() if p.name.isdigit()
            for row in [record(int(p.name), token)] if row is not None}


def identity(row):
    return row['pid'], row['start'], row['uid']


def live(row):
    return row is not None and row['state'] not in ('Z', 'X', 'x')


class OwnedProcesses:
    """Only new tagged processes or descendants of a still-matching owned parent."""
    def __init__(self, baseline, token):
        self.baseline = {identity(row) for row in baseline.values()}
        self.token, self.rows = token, {}

    def add(self, row):
        if row and row['uid'] == os.getuid() and identity(row) not in self.baseline:
            self.rows.setdefault(identity(row), row)

    def observe(self, current):
        for row in current.values():
            if row.get('tagged'):
                self.add(row)
        while True:
            parents = {row['pid'] for row in current.values() if live(row) and identity(row) in self.rows}
            count = len(self.rows)
            for row in current.values():
                if row['parent'] in parents:
                    self.add(row)
            if len(self.rows) == count:
                return

    def remaining(self, current):
        return [row for row in current.values() if live(row) and identity(row) in self.rows]

    def send(self, row, sig):
        require(identity(row) in self.rows and identity(row) not in self.baseline, 'Refusing an unowned process')
        require(row['uid'] == os.getuid(), 'Refusing a different user process')
        try:
            descriptor = os.pidfd_open(row['pid'])
        except ProcessLookupError:
            return False
        try:
            current = record(row['pid'])
            if not live(current) or identity(current) != identity(row):
                return False
            signal.pidfd_send_signal(descriptor, sig)
            return True
        except ProcessLookupError:
            return False
        finally:
            os.close(descriptor)


def private_directory(path):
    info = Path(path).lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'Directory is not private and owned')
    return info.st_dev, info.st_ino


def configs(runtime, limit):
    """Never follow links; read only the newly created private runtime's immediate children."""
    result = {}
    for folder in sorted(runtime.iterdir()):
        if folder.name == 'unrelated-sentinel':
            continue
        require(folder.name.startswith('agterm-ghostty-'), 'Unexpected shared-runtime entry')
        private_directory(folder)
        files = {}
        for path in sorted(folder.iterdir()):
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600, 'Generated input is not a private regular file')
            files[path.name] = dict(sha256=sha256(path), bytes=info.st_size, mode=0o600)
        require(0 < len(files) <= limit, 'Retained config file bound exceeded or empty owner directory')
        result[folder.name] = files
    return result


class Probe:
    def __init__(self, payload, output, base_env, expected, limit):
        self.payload, self.output, self.expected, self.limit = payload, output, expected, limit
        self.desktop = dict(base_env)
        self.token = uuid.uuid4().hex
        self.owned = OwnedProcesses(snapshot(), self.token)
        self.apps, self.children, self.commands, self.sockets = [], [], 0, None
        self.state_links = []
        self.deadline = time.monotonic() + LIMITS['total']
        self.runtime = output / 'shared-runtime'
        self.runtime.mkdir(mode=0o700)
        self.sentinel = self.runtime / 'unrelated-sentinel'
        self.sentinel.write_bytes(b'preserve unrelated runtime entry\n')
        self.sentinel_hash = sha256(self.sentinel)
        self.report = dict(schema='agterm-config-runtime-probe/1', passed=False, started_at_utc=stamp(),
                           expected_app_sha256=expected, retained_file_limit=limit, checks=[], apps=[],
                           forced_recovery=False, cleanup_actions=[], errors=[],
                           limits=['No desktop appearance switch or conditional-side transition is claimed.',
                                   'Mapped realized surfaces and PTY read-back are checked; pixels are a separate gate.',
                                   'Linux config.reload exposes no diagnostic count; app logs are checked separately.'])

    def collect(self):
        current = snapshot(self.token)
        self.owned.observe(current)
        return current

    def persist(self):
        self.report['tracked_processes'] = list(self.owned.rows.values())
        temporary = self.output / 'report.tmp'
        temporary.write_text(json.dumps(self.report, indent=2) + '\n')
        os.replace(temporary, self.output / 'report.json')

    def wait(self, check, reason, seconds=20, app=None):
        deadline = min(self.deadline, time.monotonic() + seconds)
        while time.monotonic() < deadline:
            self.collect()
            if app is not None:
                require(app['process'].poll() is None, 'App exited while waiting for ' + reason)
            value = check()
            if value:
                return value
            time.sleep(0.05)
        raise TimeoutError(reason)

    def command(self, arguments, env, *, seconds=None):
        self.commands += 1
        prefix = self.output / f'command-{self.commands:03d}'
        with prefix.with_suffix('.out').open('xb') as stdout, prefix.with_suffix('.err').open('xb') as stderr:
            child = subprocess.Popen(arguments, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            self.children.append(child)
            self.owned.add(record(child.pid))
            self.wait(lambda: child.poll() is not None, 'control/helper command completion', seconds or LIMITS['command'])
        require(child.returncode == 0, 'Control/helper command failed: ' + arguments[0])
        require(prefix.with_suffix('.out').stat().st_size <= 8 * 1024 * 1024, 'Excessive command output')
        return prefix.with_suffix('.out').read_text()

    def ctl(self, app, *arguments):
        result = json.loads(self.command([str(self.payload / 'bin/agtermctl'), *arguments,
                                         '--socket', str(app['socket']), '--json'], app['env']))
        require(result.get('ok'), 'Control response failed: ' + ' '.join(arguments[:2]))
        return result.get('result', {})

    def sessions(self, app):
        return [session for workspace in self.ctl(app, 'tree')['tree']['workspaces']
                for session in workspace['sessions']]

    def launch(self, name):
        if self.sockets is None:
            self.sockets = Path(tempfile.mkdtemp(prefix='agt-cfg-', dir='/tmp'))
            self.socket_identity = private_directory(self.sockets)
        home, state = self.output / (name + '-home'), self.output / (name + '-state')
        home.mkdir(mode=0o700); state.mkdir(mode=0o700)
        # Linux derives its socket from AGTERM_STATE_DIR, ignoring AGTERM_CONTROL_SOCKET.
        # A private short alias keeps sun_path bounded while state evidence stays in output.
        state_link = self.sockets / name
        state_link.symlink_to(state, target_is_directory=True)
        link_info = state_link.lstat()
        self.state_links.append((state_link, (link_info.st_dev, link_info.st_ino), state, private_directory(state)))
        (state / 'config').mkdir(mode=0o700)
        (state / 'settings.json').write_text(json.dumps(dict(welcomeShown=True, restoreMode='none',
                                                           closeGraceUndoEnabled=False, notificationsEnabled=False)))
        (state / 'config/ghostty.conf').write_text('title = config-probe-' + name + '\n')
        env = {key: value for key, value in self.desktop.items()
               if not key.startswith(('AGTERM_', 'GHOSTTY_', 'ZMX_', 'LD_'))
               and key not in ('GDK_DEBUG', 'GDK_DISABLE', 'GTK_DEBUG', 'GSK_DEBUG', 'ZDOTDIR', 'ENV', 'BASH_ENV')}
        display = Path(self.desktop['WAYLAND_DISPLAY'])
        if not display.is_absolute():
            display = Path(self.desktop['XDG_RUNTIME_DIR']) / display
        display = display.resolve(strict=True)
        require(display.is_socket(), 'Wayland endpoint is not a socket')
        socket = state_link / 'agterm.sock'
        require(len(os.fsencode(socket)) < 100, 'Isolated state-derived control socket path is too long')
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'),
                   XDG_CACHE_HOME=str(home / '.cache'), XDG_STATE_HOME=str(home / '.local/state'),
                   XDG_RUNTIME_DIR=str(self.runtime), WAYLAND_DISPLAY=str(display),
                   AGTERM_STATE_DIR=str(state_link), GSETTINGS_BACKEND='memory',
                   AGTERM_APP_ID='io.github.melonamin.agterm.configprobe.r' + uuid.uuid4().hex,
                   AGTERM_GHOSTTY_RESOURCES=str(self.payload / 'share/ghostty'),
                   AGTERM_RESOURCE_ROOT=str(self.payload / 'share/agterm'), AGTERM_ZMX_PATH=str(self.payload / 'bin/zmx'),
                   GDK_BACKEND='wayland', XDG_SESSION_TYPE='wayland', LIBGL_ALWAYS_SOFTWARE='1', **{TOKEN: self.token})
        app = dict(name=name, env=env, state=state, socket=socket, log=self.output / (name + '.log'))
        with app['log'].open('xb') as log:
            app['process'] = subprocess.Popen([str(self.payload / 'bin/agterm-linux')], env=env,
                                              stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.apps.append(app)
        self.children.append(app['process'])
        app['identity'] = record(app['process'].pid)
        self.owned.add(app['identity'])
        require(app['identity'] is not None, 'Missing launched process identity')
        self.persist()
        self.wait(socket.is_socket, 'isolated control socket', LIMITS['ready'], app)
        actual = sha256(Path('/proc') / str(app['process'].pid) / 'exe')
        require(actual == self.expected, 'Running app image differs from candidate')
        current = record(app['process'].pid)
        require(current and identity(current) == identity(app['identity']), 'App identity changed during launch')
        def realized():
            return next((s for s in self.sessions(app) if s.get('realized')), None)
        first = self.wait(realized, 'realized terminal surface', LIMITS['ready'], app)
        app['session'] = first['id']
        clients = json.loads(self.command(['hyprctl', 'clients', '-j'], self.desktop))
        client = next((c for c in clients if c.get('pid') == app['process'].pid and c.get('mapped')), None)
        require(client is not None and not client.get('xwayland'), 'App is not mapped on native Wayland')
        self.report['apps'].append(dict(name=name, process=app['identity'], sha256=actual,
                                        native_wayland=True, realized=True))
        self.input(app, 'initial-' + name)
        return app

    def input(self, app, label, code=None):
        marker = app['state'] / ('executed-' + uuid.uuid4().hex)
        visible = 'AGTERM_CONFIG_' + label
        text = (code + '; ' if code else '') + 'printf ' + shlex.quote(visible + '\n')
        text += '; printf done > ' + shlex.quote(str(marker)) + '\n'
        self.ctl(app, 'session', 'type', text, '--target', app['session'])
        self.wait(marker.exists, 'PTY execution marker', LIMITS['ready'], app)
        screen = self.ctl(app, 'session', 'text', '--target', app['session'])['text']
        require(visible in screen, 'PTY output is absent from screen read-back')

    def stable_configs(self, count):
        previous, matches = None, 0
        def check():
            nonlocal previous, matches
            try:
                value = configs(self.runtime, self.limit)
            except (RuntimeError, FileNotFoundError):
                previous, matches = None, 0
                return None
            matches = matches + 1 if value == previous else 0
            previous = value
            return value if len(value) == count and matches >= 3 else None
        return self.wait(check, 'stable bounded generated files', 5)

    def stop(self, app):
        self.collect()
        require(app['process'].poll() is None, 'App exited before requested shutdown')
        require(self.owned.send(app['identity'], signal.SIGTERM), 'App identity vanished before shutdown')
        started = time.monotonic()
        self.wait(lambda: app['process'].poll() is not None, 'graceful app shutdown', LIMITS['shutdown'])
        require(app['process'].returncode == 0, 'App shutdown returned a failure')
        self.report['apps'][self.apps.index(app)]['shutdown_seconds'] = time.monotonic() - started

    def exercise(self):
        left = self.launch('left')
        left_files = self.stable_configs(1)
        right = self.launch('right')
        both = self.stable_configs(2)
        require(all(both.get(name) == files for name, files in left_files.items()), 'Second instance altered first inputs')
        right_files = {name: files for name, files in both.items() if name not in left_files}
        require(len(right_files) == 1, 'Instances did not obtain separate config directories')
        self.report['checks'].append('two exact mapped instances with private disjoint config paths and PTY execution')
        self.stop(left)
        remaining = self.stable_configs(1)
        require(remaining == right_files and all(not (self.runtime / name).exists() for name in left_files),
                'First cleanup removed peer files or retained its own directory')
        self.input(right, 'peer-survived')
        self.report['checks'].append('idle first shutdown cleans its config while peer remains usable')
        for number in range(32):
            (right['state'] / 'config/ghostty.conf').write_text(f'title = config-probe-reload-{number}\n')
            self.ctl(right, 'config', 'reload')
        colors = "for i in $(seq 1 32); do printf '\\033]11;#223344\\007\\033]111\\007'; done"
        self.input(right, 'backgrounds', colors)
        after = self.stable_configs(1)
        require(after == right_files, 'Repeated config/OSC updates changed retained theme inputs or grew storage')
        for folder, files in after.items():
            require(len(files) == 2 and all(f['bytes'] == 0 for f in files.values()),
                    'Expected one retained empty light/dark theme pair')
            require(any('light' in name.lower() for name in files) and any('dark' in name.lower() for name in files),
                    'Retained inputs do not identify both conditional themes')
        self.report['retained_files'] = after
        self.report['checks'].append('32 config reloads and OSC background updates preserve bounded usable theme files')
        producer, started = right['state'] / 'noise.py', right['state'] / 'noise-started.json'
        producer.write_text("import json,os,pathlib,sys,time\n"
                            f"marker=pathlib.Path({str(started)!r})\n"
                            "print('AGTERM_CONFIG_NOISE_STARTED', flush=True)\n"
                            "end=time.monotonic()+8\ni=0\n"
                            "while time.monotonic()<end:\n"
                            " sys.stdout.write('\\033]2;AGTERM_CONFIG_NOISE_%d\\007'%i); sys.stdout.flush(); i+=1\n"
                            " if i==100:\n"
                            "  marker.write_text(json.dumps({'pid':os.getpid(),'titles_sent':i}))\n")
        self.ctl(right, 'session', 'type', 'python3 ' + shlex.quote(str(producer)) + '\n', '--target', right['session'])
        def started_pid():
            try:
                value = json.loads(started.read_text())
                pid = value['pid']
                return pid if type(pid) is int and pid > 0 and value.get('titles_sent') == 100 else None
            except (OSError, ValueError, KeyError):
                return None  # creation can be observed before write_text finishes
        noise_pid = self.wait(started_pid, 'noisy producer start', 5, right)
        current = self.collect()
        noise = current.get(noise_pid)
        require(live(noise) and identity(noise) in self.owned.rows, 'Noisy producer is not a live owned process')
        require('AGTERM_CONFIG_NOISE_STARTED' in self.ctl(right, 'session', 'text', '--target', right['session'])['text'],
                'Noisy producer output not observed')
        require(live(record(noise_pid)), 'Noisy producer ended before shutdown')
        self.report['noisy_producer'] = noise
        self.report['noisy_titles_written_before_shutdown'] = 100
        self.stop(right)
        self.wait(lambda: list(self.runtime.iterdir()) == [self.sentinel], 'all generated config directories removed', 5)
        require(sha256(self.sentinel) == self.sentinel_hash, 'Unrelated runtime sentinel changed')
        self.report['checks'].append('bounded noisy-title shutdown cleans generated files and preserves sentinel')

    def cleanup(self):
        """Recover only known new identities. Never turn recovery into a passing observation."""
        current = self.collect()
        remaining = self.owned.remaining(current)
        # Reap an ordinarily exiting shell/producer before classifying intervention as recovery.
        deadline = time.monotonic() + LIMITS['drain']
        while remaining and all(app['process'].poll() is not None for app in self.apps) and time.monotonic() < deadline:
            for child in self.children:
                child.poll()
            time.sleep(0.05)
            remaining = self.owned.remaining(self.collect())
        if remaining:
            self.report['forced_recovery'] = True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for row in remaining:
                    if self.owned.send(row, sig):
                        self.report['cleanup_actions'].append(dict(process=row, signal=int(sig)))
                deadline = time.monotonic() + LIMITS['recovery']
                while time.monotonic() < deadline:
                    for child in self.children:
                        child.poll()
                    remaining = self.owned.remaining(self.collect())
                    if not remaining:
                        break
                    time.sleep(0.05)
                if not remaining:
                    break
        self.report['remaining_processes'] = remaining
        self.report['sentinel_preserved'] = self.sentinel.exists() and sha256(self.sentinel) == self.sentinel_hash
        self.report['remaining_generated_paths'] = sorted(p.name for p in self.runtime.iterdir() if p != self.sentinel)
        for app in self.apps:
            require(app['log'].stat().st_size <= 16 * 1024 * 1024, 'Excessive app diagnostic output')
            data = app['log'].read_text(errors='replace')
            if re.search(r'generated Ghostty config|failed to load.*theme|error.*config', data, re.I):
                self.report['errors'].append(app['name'] + ': generated-config diagnostics in app log')
        if self.sockets is not None and not remaining:
            require(private_directory(self.sockets) == self.socket_identity, 'Socket directory identity changed')
            for link, expected_link, state, expected_state in self.state_links:
                info = link.lstat()
                require(stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid()
                        and (info.st_dev, info.st_ino) == expected_link and os.readlink(link) == str(state),
                        'State alias identity or target changed')
                require(private_directory(state) == expected_state, 'State directory identity changed')
                path = state / 'agterm.sock'
                if os.path.lexists(path):
                    info = path.lstat()
                    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), 'Unexpected socket replacement')
                    path.unlink()
                link.unlink()
            self.sockets.rmdir()  # refuses unrelated entries; never recursively delete
        self.report['socket_directory_removed'] = self.sockets is None or not self.sockets.exists()


def run_probe(payload, output_dir, base_env, expected_app_sha256, *, retained_file_limit=2):
    """Reject unsafe invocation before creating output; execution failures return a persisted failed report."""
    require(os.uname().sysname == 'Linux' and os.getuid() != 0, 'Run as the unprivileged Linux desktop user')
    require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Linux pidfd APIs are required')
    require(re.fullmatch(r'[0-9a-f]{64}', expected_app_sha256), 'Expected app SHA256 is required')
    require(type(retained_file_limit) is int and retained_file_limit == 2, 'Corrected contract retains exactly two theme files')
    payload, output = Path(payload), Path(output_dir)
    require(payload.is_absolute() and payload.resolve(strict=True) == payload, 'Payload path must be canonical')
    require(output.is_absolute() and output.parent.resolve(strict=True) == output.parent, 'Output parent must be canonical')
    private_directory(output.parent)
    require(not os.path.lexists(output), 'Output directory already exists')
    require(sha256(payload / 'bin/agterm-linux.bin') == expected_app_sha256, 'Payload app digest mismatch')
    require(all(os.access(payload / 'bin' / name, os.X_OK) for name in ('agterm-linux', 'agtermctl')), 'Missing payload wrappers')
    require(all(base_env.get(key) for key in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS')), 'Incomplete desktop route')
    before_env, shell = dict(os.environ), pwd.getpwuid(os.getuid()).pw_shell
    output.mkdir(mode=0o700)
    probe = Probe(payload, output, base_env, expected_app_sha256, retained_file_limit)
    previous, received = {}, []
    def interrupted(sig, frame):
        received.append(sig)
        raise InterruptedError('Probe interrupted')
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, interrupted)
        probe.persist()
        probe.exercise()
        probe.report['passed'] = True
    except BaseException as error:
        probe.report['errors'].append(type(error).__name__ + ': ' + str(error))
    finally:
        # A second interruption is recorded without interrupting bounded recovery.
        for sig in previous:
            signal.signal(sig, lambda value, frame: received.append(value))
        try:
            probe.cleanup()
        except BaseException as error:
            probe.report['errors'].append('cleanup: ' + type(error).__name__ + ': ' + str(error))
        probe.report.update(ended_at_utc=stamp(), signals=received,
                            caller_environment_unchanged=dict(os.environ) == before_env,
                            login_shell_unchanged=pwd.getpwuid(os.getuid()).pw_shell == shell)
        probe.report['passed'] = bool(probe.report['passed'] and not probe.report['errors'] and not received
                                     and len(probe.report['checks']) == 4 and len(probe.report['apps']) == 2
                                     and not probe.report['forced_recovery'] and not probe.report.get('remaining_processes')
                                     and not probe.report.get('remaining_generated_paths') and probe.report.get('sentinel_preserved')
                                     and probe.report.get('socket_directory_removed') and probe.report['caller_environment_unchanged']
                                     and probe.report['login_shell_unchanged'])
        try:
            probe.persist()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return probe.report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--app-sha256', required=True)
    args = parser.parse_args()
    result = run_probe(args.payload, args.output_dir, dict(os.environ), args.app_sha256)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
