#!/usr/bin/env python3
"""Rerun a few named GTK AT-SPI scenarios, each in its own desktop session.

This is a diagnosis aid, not acceptance: the complete suite in ci.yml (test-linux-ui-parallel.py) stays the
only AT-SPI result a merge may rely on. Names are checked against the `scenarios` tuple in the target
checkout's atspi_smoke.py, read with `ast` so validation needs neither GTK nor a build.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


SMOKE = 'agterm-linux/tests/atspi_smoke.py'
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def known_scenarios(root):
    tree = ast.parse((root / SMOKE).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    assignment = next(node for node in ast.walk(main) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'scenarios' for target in node.targets))
    return tuple(ast.literal_eval(assignment.value))


def select(requested, known):
    """Return the requested names in order without duplicates, or raise ValueError naming every bad one."""
    names = [name.strip() for name in requested.split(',')]
    if not requested.strip() or any(not name for name in names):
        raise ValueError('scenarios must be a comma-separated list of names with no empty entries')
    unknown = [name for name in names if not NAME.fullmatch(name) or name not in known]
    if unknown:
        raise ValueError('unknown AT-SPI scenario(s): ' + ', '.join(repr(name) for name in unknown))
    return list(dict.fromkeys(names))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('scenarios', help='comma-separated scenario names, e.g. normal,context-menu')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent,
                        help='checkout whose atspi_smoke.py and test-linux-ui.sh are used')
    parser.add_argument('--check-only', action='store_true', help='validate the names and exit')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        selected = select(args.scenarios, known_scenarios(root))
    except (OSError, SyntaxError, StopIteration, ValueError) as error:
        print(f'FAIL: {str(error) or type(error).__name__}', file=sys.stderr)
        return 2
    print('selected AT-SPI scenarios: ' + ','.join(selected), flush=True)
    if args.check_only:
        return 0

    artifacts = Path(os.environ.get('AGTERM_UI_ARTIFACT_DIR', root / 'artifacts/linux-ui')).resolve()
    results = {}
    started = time.monotonic()
    for scenario in selected:
        directory = artifacts / scenario
        directory.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, AGTERM_ATSPI_SCENARIO=scenario, AGTERM_UI_ARTIFACT_DIR=str(directory))
        for inherited in ('AGTERM_RESOURCE_ROOT', 'AGTERM_ATSPI_SHARD_INDEX', 'AGTERM_ATSPI_SHARD_COUNT'):
            env.pop(inherited, None)
        scenario_started = time.monotonic()
        returncode = subprocess.run([str(root / 'scripts/test-linux-ui.sh')], env=env, cwd=root).returncode
        results[scenario] = returncode
        print(f'AT-SPI scenario {scenario}: status={returncode} '
              f'elapsed_seconds={time.monotonic() - scenario_started:.3f}', flush=True)
    print(json.dumps({'scenarios': results, 'elapsed_seconds': round(time.monotonic() - started, 3)}), flush=True)
    failed = [name for name, code in results.items() if code]
    if failed:
        print('FAILED: ' + ', '.join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
