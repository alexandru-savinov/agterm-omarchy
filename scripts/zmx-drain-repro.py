#!/usr/bin/env python3
"""Count how often a zmx attach client loses a session's last output line when the session ends.

Each run attaches a fresh session under a pty whose command prints a start mark, a burst of filler,
a final mark, and exits. The pty master is read to EOF; a run whose captured bytes hold the start mark
but not the final mark lost its last line. `--reader slow` reads the pty in small, spaced chunks, the
way a terminal that keeps falling behind does; `--reader stall` reads fast but stops reading for
STALL_S once the start mark arrives, the way a terminal busy rendering does, then reads fast to EOF;
`--reader fast` reads as fast as it can.

usage: zmx-drain-repro.py ZMX_BINARY --runs N --reader fast|slow|stall [--burst BYTES]
Prints one line per run and a summary line `RESULT reader=<r> runs=<n> lost=<k> invalid=<i>`.
Exits 0 whatever the count; the count is the result.
"""
import argparse
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

START = b"DRAIN-START-MARK"
FINAL = b"DRAIN-FINAL-MARK"
RUN_TIMEOUT_S = 60
STALL_S = 0.5


def run_once(zmx, index, reader, burst, sock_dir):
    name = "drain%03d" % index
    command = (
        "sleep 0.3; printf 'DRAIN-START-MARK\\n'; "
        "head -c %d /dev/zero | tr '\\0' x | fold -w 100; "
        "printf 'DRAIN-FINAL-MARK\\n'; exit 3" % burst
    )
    env = dict(os.environ, ZMX_DIR=sock_dir, TERM="xterm-256color")
    env.pop("ZMX_SESSION", None)
    env.pop("ZMX_MANAGED", None)
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execve(zmx, [zmx, "attach", name, "/bin/sh", "-c", command], env)
        finally:
            os._exit(127)
    captured = bytearray()
    deadline = time.monotonic() + RUN_TIMEOUT_S
    timed_out = False
    chunk, pause = (1024, 0.002) if reader == "slow" else (65536, 0.0)
    stalled = reader != "stall"
    while True:
        if time.monotonic() > deadline:
            timed_out = True
            os.kill(pid, signal.SIGKILL)
            break
        ready, _, _ = select.select([master], [], [], 1.0)
        if not ready:
            continue
        try:
            data = os.read(master, chunk)
        except OSError:
            break  # EIO: every slave fd is closed and the master is drained
        if not data:
            break
        captured.extend(data)
        if not stalled and START in captured:
            stalled = True
            time.sleep(STALL_S)
        if pause:
            time.sleep(pause)
    _, status = os.waitpid(pid, 0)
    os.close(master)
    subprocess.run([zmx, "kill", name, "--force"], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    has_start = START in captured
    has_final = FINAL in captured
    if timed_out or not has_start:
        verdict = "INVALID"
    elif has_final:
        verdict = "KEPT"
    else:
        verdict = "LOST"
    tail = bytes(captured[-120:]).replace(b"\x1b", b"\\e")
    print("run=%d verdict=%s bytes=%d status=%d timed_out=%s tail=%r"
          % (index, verdict, len(captured), status, timed_out, tail), flush=True)
    return verdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zmx")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--reader", choices=("fast", "slow", "stall"), default="fast")
    parser.add_argument("--burst", type=int, default=200000)
    args = parser.parse_args()
    zmx = os.path.abspath(args.zmx)
    sock_dir = tempfile.mkdtemp(prefix="zmxdr", dir="/tmp")
    counts = {"KEPT": 0, "LOST": 0, "INVALID": 0}
    try:
        for i in range(args.runs):
            counts[run_once(zmx, i, args.reader, args.burst, sock_dir)] += 1
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)
    print("RESULT reader=%s runs=%d lost=%d kept=%d invalid=%d burst=%d"
          % (args.reader, args.runs, counts["LOST"], counts["KEPT"], counts["INVALID"], args.burst),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
