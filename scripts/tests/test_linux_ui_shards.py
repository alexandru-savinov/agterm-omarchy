#!/usr/bin/env python3
"""Check suite partitioning and isolated worker lifetime without GTK or an app build."""
import ast
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class SuitePartitionTests(unittest.TestCase):
    def setUp(self):
        source = ROOT / 'agterm-linux/tests/atspi_smoke.py'
        tree = ast.parse(source.read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        assignment = next(node for node in ast.walk(main) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'scenarios' for target in node.targets))
        self.scenarios = ast.literal_eval(assignment.value)
        self.calls = []
        self.failed = None

        def run(command, env):
            scenario = env['AGTERM_ATSPI_SCENARIO']
            self.calls.append(scenario)
            return SimpleNamespace(returncode=7 if scenario == self.failed else 0)

        namespace = dict(os=os, sys=sys, subprocess=SimpleNamespace(run=run),
                         BIN=__file__, CTL=__file__, __file__=str(source))
        exec(compile(ast.Module(body=[main], type_ignores=[]), str(source), 'exec'), namespace)
        self.main = namespace['main']

    def execute(self, **env):
        self.calls.clear()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            result = self.main()
        return result, list(self.calls)

    def test_two_shards_cover_every_scenario_once_in_original_order(self):
        partitions = [self.execute(AGTERM_ATSPI_SHARD_INDEX=str(index), AGTERM_ATSPI_SHARD_COUNT='2')
                      for index in range(2)]
        self.assertEqual([result for result, _ in partitions], [0, 0])
        self.assertEqual(partitions[0][1], list(self.scenarios[::2]))
        self.assertEqual(partitions[1][1], list(self.scenarios[1::2]))
        self.assertCountEqual(partitions[0][1] + partitions[1][1], self.scenarios)
        self.assertEqual(len(set(self.scenarios)), len(self.scenarios))
        self.assertEqual(self.execute(), (0, list(self.scenarios)))

    def test_invalid_partition_fails_before_running_any_scenario(self):
        for index, count in (('-1', '2'), ('2', '2'), ('0', '0'), ('no', '2'), ('0', 'no'),
                             ('0', str(len(self.scenarios) + 1))):
            with self.subTest(index=index, count=count):
                self.assertEqual(self.execute(AGTERM_ATSPI_SHARD_INDEX=index, AGTERM_ATSPI_SHARD_COUNT=count), (2, []))

    def test_failed_scenario_does_not_skip_the_rest_of_its_shard(self):
        self.failed = self.scenarios[0]
        self.assertEqual(self.execute(AGTERM_ATSPI_SHARD_INDEX='0', AGTERM_ATSPI_SHARD_COUNT='2'),
                         (7, list(self.scenarios[::2])))


class WorkerLifetimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        worker = self.work / 'worker.py'
        worker.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, signal, subprocess, sys, time
directory = pathlib.Path(os.environ['AGTERM_UI_ARTIFACT_DIR'])
index = os.environ['AGTERM_ATSPI_SHARD_INDEX']
assert 'AGTERM_RESOURCE_ROOT' not in os.environ
if os.environ.get('CANCEL_FIXTURE'):
    child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    child = None
(directory / 'started.json').write_text(json.dumps({'pid': os.getpid(), 'child': child.pid if child else None,
                                                   'index': index, 'count': os.environ['AGTERM_ATSPI_SHARD_COUNT']}))
if os.environ.get('EXIT_LEADER') == index:
    sys.exit(0)
deadline = time.monotonic() + 5
while not (directory.parent / ('shard-1' if index == '0' else 'shard-0') / 'started.json').exists():
    if time.monotonic() > deadline: sys.exit(9)
    time.sleep(0.01)
if child:
    child.wait()
else:
    time.sleep(0.05 if index == '0' else 0.2)
    (directory / 'completed').write_text('done')
    sys.exit(7 if index == os.environ.get('FAIL_SHARD') else 0)
''')
        worker.chmod(0o755)
        self.command = [sys.executable, '-c',
                        'import runpy,sys; module=runpy.run_path(sys.argv[1]); '
                        'module["main"].__globals__["SCRIPT"]=sys.argv[2]; sys.exit(module["main"]())',
                        str(ROOT / 'scripts/test-linux-ui-parallel.py'), str(worker)]
        self.env = dict(os.environ, AGTERM_UI_ARTIFACT_DIR=str(self.work / 'artifacts'),
                        AGTERM_RESOURCE_ROOT='/must-not-share', PYTHONDONTWRITEBYTECODE='1')
        for name in ('AGTERM_ATSPI_SCENARIO', 'CANCEL_FIXTURE', 'FAIL_SHARD', 'EXIT_LEADER'):
            self.env.pop(name, None)

    def test_workers_overlap_and_failure_waits_for_both_diagnostics(self):
        for failure in ('', '0', '1'):
            with self.subTest(failure=failure):
                result = subprocess.run(self.command, env=dict(self.env, FAIL_SHARD=failure),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1 if failure else 0, result.stderr)
                for index in range(2):
                    directory = self.work / 'artifacts' / f'shard-{index}'
                    self.assertTrue((directory / 'completed').exists())
                    report = json.loads((directory / 'started.json').read_text())
                    self.assertEqual((report['index'], report['count']), (str(index), '2'))
                    (directory / 'completed').unlink()
                    (directory / 'started.json').unlink()

    def test_single_scenario_override_is_rejected(self):
        result = subprocess.run(self.command, env=dict(self.env, AGTERM_ATSPI_SCENARIO='normal'),
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / 'artifacts').exists())

    def assert_interrupted_cleanup(self, exited_leader=''):
        process = subprocess.Popen(self.command, env=dict(self.env, CANCEL_FIXTURE='1', EXIT_LEADER=exited_leader),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            reports = [self.work / 'artifacts' / f'shard-{index}' / 'started.json' for index in range(2)]
            deadline = time.monotonic() + 5
            while not all(report.exists() for report in reports):
                self.assertLess(time.monotonic(), deadline, 'workers did not start')
                time.sleep(0.01)
            identities = [json.loads(report.read_text()) for report in reports]
            if exited_leader:
                leader = identities[int(exited_leader)]['pid']
                while Path(f'/proc/{leader}/stat').read_text().split(')')[1].split()[0] != 'Z':
                    self.assertLess(time.monotonic(), deadline, 'exited leader was not retained for cleanup')
                    time.sleep(0.01)
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143, output + error)
            for identity in identities:
                for pid in (identity['pid'], identity['child']):
                    stat = Path(f'/proc/{pid}/stat')
                    self.assertTrue(not stat.exists() or stat.read_text().split(')')[1].split()[0] == 'Z',
                                    f'owned process {pid} remains live')
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)

    def test_interruption_terminates_both_workers_and_their_descendants(self):
        self.assert_interrupted_cleanup()

    def test_interruption_cleans_descendants_of_an_already_exited_leader(self):
        self.assert_interrupted_cleanup(exited_leader='0')

    def test_disappearing_groups_do_not_prevent_other_cleanup_or_reaping(self):
        spec = importlib.util.spec_from_file_location('ui_parallel', ROOT / 'scripts/test-linux-ui-parallel.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        children = [unittest.mock.Mock(pid=101, returncode=0), unittest.mock.Mock(pid=102, returncode=0)]
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(module.subprocess, 'Popen', side_effect=children), \
                patch.object(module.os, 'waitid', return_value=SimpleNamespace(si_status=0)), \
                patch.object(module.os, 'killpg', side_effect=ProcessLookupError) as kill, \
                patch.object(module.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 0)
        self.assertEqual(kill.call_count, 4)
        for child in children:
            child.wait.assert_called_once_with(timeout=5)


if __name__ == '__main__':
    unittest.main()
