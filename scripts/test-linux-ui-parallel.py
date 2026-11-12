#!/usr/bin/env python3
"""Run the complete GTK suite in two independent desktop sessions."""
import os
import json
from pathlib import Path
import signal
import subprocess
import time


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts/test-linux-ui.sh'


def main():
    if 'AGTERM_ATSPI_SCENARIO' in os.environ:
        raise ValueError('parallel acceptance requires the complete scenario suite')
    artifacts = Path(os.environ.get('AGTERM_UI_ARTIFACT_DIR', ROOT / 'artifacts/linux-ui')).resolve()
    children = []
    started = time.monotonic()
    completed = {}
    interrupted = 0

    def interrupt(number, frame):
        nonlocal interrupted
        interrupted = number

    previous = {number: signal.signal(number, interrupt)
                for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        for index in range(2):
            if interrupted:
                break
            directory = artifacts / f'shard-{index}'
            directory.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, AGTERM_ATSPI_SHARD_INDEX=str(index), AGTERM_ATSPI_SHARD_COUNT='2',
                       AGTERM_UI_ARTIFACT_DIR=str(directory))
            env.pop('AGTERM_RESOURCE_ROOT', None)
            children.append(subprocess.Popen([str(SCRIPT)], env=env, start_new_session=True))
        while not interrupted and len(completed) < len(children):
            for index, child in enumerate(children):
                if index in completed:
                    continue
                status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if status is not None:
                    completed[index] = status.si_status
                    print(f'UI shard {index}: elapsed_seconds={time.monotonic() - started:.3f} '
                          f'status={status.si_status}', flush=True)
            if len(completed) < len(children):
                time.sleep(0.1)
        if interrupted:
            return 128 + interrupted
        return 1 if any(completed.values()) else 0
    finally:
        for number in (signal.SIGTERM, signal.SIGKILL):
            for child in children:
                try:
                    os.killpg(child.pid, number)
                except ProcessLookupError:
                    pass
            if children and number == signal.SIGTERM:
                time.sleep(0.2)
        for child in children:
            child.wait(timeout=5)
        for number, handler in previous.items():
            signal.signal(number, handler)
        print(json.dumps({'ui_shards': len(children), 'elapsed_seconds': round(time.monotonic() - started, 3),
                          'returncodes': [child.returncode for child in children]}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
