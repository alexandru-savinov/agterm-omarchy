"""Observe an owned child's empty argv bytes; this does not execute Swift."""
import pathlib
import subprocess
import sys

args = [sys.executable, '-c', 'import sys; sys.stdout.write("ready\\n"); sys.stdout.flush(); sys.stdin.read()', '', 'tail', '']
child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
try:
    assert child.stdout.readline() == 'ready\n'
    data = pathlib.Path(f'/proc/{child.pid}/cmdline').read_bytes()
    expected = [a.encode() for a in args]
    preserved = data[:-1].split(b'\0')
    omitted = [part for part in data.split(b'\0') if part]
    assert preserved == expected
    assert omitted != expected
    print('actual /proc suffix:', repr(data[-8:]))
    print('empty argument indexes:', [i for i, a in enumerate(preserved) if not a])
    print('expected argc:', len(expected), 'omitting-empty argc:', len(omitted))
finally:
    child.communicate('')
