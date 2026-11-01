"""Pinned Foundation helper probe; no GTK, app payload, or VM execution."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
if len(sys.argv) != 2:
    raise SystemExit('usage: python3 probe.py /path/to/agterm-omarchy-checkout')
REPO = Path(sys.argv[1]).resolve()
COMMIT = 'fd85fc5824a782d17b2aee0cb113ef31f9ce66ab'
SOURCE = 'agterm-linux/Sources/AgtermLinux/GhosttyApp.swift'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ready(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=10):
            raise RuntimeError('probe did not report its completed write')
    line = process.stdout.readline()
    if not line:
        raise RuntimeError('probe exited before reporting its write')
    return json.loads(line)


def scenario(name, shared):
    case = ROOT / name
    case.mkdir()
    states = [case / 'state-a', case / 'state-b']
    runtimes = [case / 'runtime-a', case / ('runtime-a' if shared else 'runtime-b')]
    for path in set(states + runtimes):
        path.mkdir(mode=0o700)

    def environment(index):
        return dict(os.environ, XDG_RUNTIME_DIR=str(runtimes[index]),
                    AGTERM_STATE_DIR=str(states[index]))

    process_a = subprocess.Popen([str(ROOT / 'probe'), 'background = #112233'],
                                 env=environment(0), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        written_a = ready(process_a)
        process_b = subprocess.run([str(ROOT / 'probe'), 'background = #445566'],
                                   env=environment(1), input='read\n', text=True,
                                   capture_output=True, timeout=10, check=True)
        written_b, read_b = map(json.loads, process_b.stdout.splitlines())
        tail_a, error_a = process_a.communicate(input='read\n', timeout=10)
        if process_a.returncode:
            raise RuntimeError(error_a)
        read_a = json.loads(tail_a)
    finally:
        if process_a.poll() is None:
            process_a.kill()
            process_a.communicate(timeout=5)

    assert written_a['state'] != written_b['state']
    assert read_b['contents'] == 'background = #445566'
    assert (written_a['path'] == written_b['path']) is shared
    assert read_a['contents'] == ('background = #445566' if shared else 'background = #112233')
    return {
        'case': name,
        'distinct_state_directories': True,
        'same_returned_path': written_a['path'] == written_b['path'],
        'a_returned_path': str(Path(written_a['path']).relative_to(ROOT)),
        'b_returned_path': str(Path(written_b['path']).relative_to(ROOT)),
        'a_intended': 'background = #112233',
        'a_observed_after_b_write': read_a['contents'],
        'b_observed': read_b['contents'],
        'a_exit': process_a.returncode,
        'b_exit': process_b.returncode,
    }


source = subprocess.check_output(['git', '-C', str(REPO), 'show', f'{COMMIT}:{SOURCE}'])
text = source.decode()
start = text.index('    private static func writeTempConf(_ lines: [String]) -> String? {')
end = text.index('\n    }', start) + len('\n    }')
method = text[start:end]
assert method.count('private static func') == 1
swift = '''import Foundation
enum Probe {
''' + method + '''
    static func run() throws {
        guard CommandLine.arguments.count == 2,
              let path = writeTempConf([CommandLine.arguments[1]]) else {
            throw NSError(domain: "Probe", code: 1)
        }
        let record = ["path": path, "state": ProcessInfo.processInfo.environment["AGTERM_STATE_DIR"] ?? ""]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: record))
        FileHandle.standardOutput.write(Data([10]))
        guard readLine() == "read" else { throw NSError(domain: "Probe", code: 2) }
        let contents = try String(contentsOfFile: path, encoding: .utf8)
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["contents": contents]))
        FileHandle.standardOutput.write(Data([10]))
    }
}
try Probe.run()
'''
(ROOT / 'Probe.swift').write_text(swift)
(ROOT / 'extracted-helper.swift').write_text(method + '\n')
version = subprocess.check_output(['swiftc', '--version'], text=True).strip()
subprocess.run(['swiftc', '-module-cache-path', str(ROOT / 'module-cache'),
                str(ROOT / 'Probe.swift'), '-o', str(ROOT / 'probe')],
               check=True, timeout=90, capture_output=True, text=True)
report = {
    'source_commit': COMMIT,
    'source_path': SOURCE,
    'source_sha256': sha(source),
    'extracted_helper_sha256': sha(method.encode()),
    'probe_swift_sha256': sha(swift.encode()),
    'probe_runner_sha256': sha(Path(__file__).read_bytes()),
    'swift_version': version,
    'boundary': 'Foundation-only helper on macOS; file read models the load gap; no GTK/libghostty/native app execution',
    'results': [scenario('shared-runtime', True), scenario('separate-runtime-control', False)],
}
(ROOT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
