import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import tempfile

SOURCE = 'UNSELECTED_CANDIDATE'
SOURCE_RUN = 0
ASSETS = {
    'harness.sh': '9fe046d6fbbf0dbc79e0591b2fbe8869af4f79604d6a2f7a470c5750044cee40',
    'fixture.c': 'b96b70613a14e562eb5d2edec02653b2a41221eedaaf06880334eda3b15ca470',
    'validator.py': 'cd32706bc7454324cc092345e506385bc1e9aa9fa852d0417a1893f3f0fc24b6',
    'config-probe.py': 'c521ff81ffa6677eeb61b1601c5535cda140ee605682449d6abb63d683d6eed9',
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main(config):
    stage = None
    account = None
    report = {'passed': False, 'phase': 'preflight'}
    signals = []
    previous = {}
    def interrupted(sig, frame):
        signals.append(sig)
        raise InterruptedError('Preparation interrupted by signal ' + str(sig))
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[sig] = signal.signal(sig, interrupted)
    try:
        require(config['source_commit'] == SOURCE and config['source_run'] == SOURCE_RUN,
                'Wrong frozen source or trusted run')
        require(os.uname().sysname == 'Linux' and os.uname().machine in ('aarch64', 'arm64'),
                'Preparation requires the intended Linux ARM64 runtime')
        require(set(config['assets']) == set(ASSETS), 'Unexpected asset set')
        decoded = {}
        for name, expected in ASSETS.items():
            asset = config['assets'][name]
            data = base64.b64decode(asset['data'], validate=True)
            require(asset['sha256'] == hashlib.sha256(data).hexdigest() == expected,
                    'Wrong frozen asset: ' + name)
            decoded[name] = data
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
        require(Path('/usr/bin/zsh').is_file(), 'Missing test zsh')
        parent = Path(account.pw_dir) / 'src/agterm-logs'
        require(parent.is_dir() and parent.resolve() == parent and parent.stat().st_uid == os.getuid(),
                'Staging parent is not the intended owned directory')
        stage = Path(tempfile.mkdtemp(prefix='native-config-isolation.', dir=parent))
        require(stat.S_IMODE(stage.stat().st_mode) == 0o700, 'Stage is not private')
        report.update(stage=str(stage), phase='prepare')
        for name, data in decoded.items():
            with (stage / name).open('xb') as handle:
                handle.write(data)
        (stage / 'candidate.tar.gz').touch(mode=0o600, exist_ok=False)
        with (stage / 'fixture-build.log').open('xb') as output:
            compiler = subprocess.Popen(['/usr/bin/cc', '-shared', '-fPIC', '-o', str(stage / 'fixture.so'),
                                         str(stage / 'fixture.c'), '-ldl'], stdout=output,
                                        stderr=subprocess.STDOUT, start_new_session=True)
            try:
                require(compiler.wait(timeout=30) == 0, 'Fixture compilation failed')
            except BaseException:
                if compiler.poll() is None:
                    # This newly created group contains only this fixture compiler and its children.
                    os.killpg(compiler.pid, signal.SIGKILL)
                    compiler.wait(timeout=5)
                raise
        public = {key: config[key] for key in ('source_commit', 'source_run')}
        public['assets'] = {name: {k: v for k, v in value.items() if k != 'data'}
                            for name, value in config['assets'].items()}
        public['fixture_binary_sha256'] = hashlib.sha256((stage / 'fixture.so').read_bytes()).hexdigest()
        public['real_login_shell'] = account.pw_shell
        (stage / 'preparation.json').write_text(json.dumps(public, indent=2) + '\n')
        report.update(passed=True, phase='complete', **public)
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        report['signals'] = signals
        try:
            report['login_shell_unchanged'] = None if account is None else pwd.getpwuid(os.getuid()).pw_shell == account.pw_shell
        except BaseException as error:
            report.update(login_shell_unchanged=False, preservation_error=type(error).__name__ + ': ' + str(error))
        report['passed'] = bool(report['passed'] and report['login_shell_unchanged'] and not signals)
        report['recovery'] = {'stage_retained': stage is not None, 'native_test_started': False}
        if stage is not None:
            try:
                (stage / 'preparation-status.json').write_text(json.dumps(report, indent=2) + '\n')
            except BaseException as error:
                report.update(passed=False, report_write_error=type(error).__name__ + ': ' + str(error))
        print(json.dumps(report), flush=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main(CONFIG))
