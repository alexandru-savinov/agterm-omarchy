#!/usr/bin/env python3
"""Check targeted AT-SPI scenario selection and its per-scenario runs without GTK or an app build."""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ui_targeted', ROOT / 'scripts/test-linux-ui-targeted.py')
targeted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(targeted)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.known = targeted.known_scenarios(ROOT)

    def test_reads_the_real_suite(self):
        self.assertIn('normal', self.known)
        self.assertIn('context-menu', self.known)
        self.assertGreater(len(self.known), 50)
        self.assertEqual(len(set(self.known)), len(self.known))

    def test_known_names_keep_order_and_drop_duplicates(self):
        self.assertEqual(targeted.select(' context-menu,normal ,context-menu', self.known),
                         ['context-menu', 'normal'])

    def test_unknown_or_empty_entries_are_rejected(self):
        for requested in ('', ' ', ',', 'normal,', 'normal,,context-menu', 'no-such-scenario',
                          'normal,no-such-scenario', 'Normal', 'normal;id', '../normal'):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                targeted.select(requested, self.known)

    def test_unknown_name_fails_before_any_run(self):
        with patch.object(targeted.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(targeted.main(['normal,no-such-scenario']), 2)
        run.assert_not_called()
        self.assertIn("'no-such-scenario'", error.getvalue())

    def test_check_only_runs_nothing(self):
        with patch.object(targeted.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(targeted.main(['normal', '--check-only']), 0)
        run.assert_not_called()
        self.assertIn('selected AT-SPI scenarios: normal', out.getvalue())


class RunTests(unittest.TestCase):
    """A fake checkout whose test-linux-ui.sh records each call proves isolation and failure handling."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'agterm-linux/tests').mkdir(parents=True)
        (self.root / 'scripts').mkdir()
        (self.root / targeted.SMOKE).write_text('def main():\n    scenarios = ("alpha", "beta", "gamma")\n')
        runner = self.root / 'scripts/test-linux-ui.sh'
        runner.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
record = {name: os.environ.get(name) for name in ('AGTERM_ATSPI_SCENARIO', 'AGTERM_UI_ARTIFACT_DIR',
          'AGTERM_RESOURCE_ROOT', 'AGTERM_ATSPI_SHARD_INDEX', 'AGTERM_ATSPI_SHARD_COUNT')}
with open(os.environ['CALLS'], 'a') as calls:
    calls.write(json.dumps(record) + '\\n')
sys.exit(5 if record['AGTERM_ATSPI_SCENARIO'] == os.environ.get('FAIL_SCENARIO') else 0)
''')
        runner.chmod(0o755)
        self.calls = self.root / 'calls.jsonl'
        self.env = {'PATH': os.environ.get('PATH', ''), 'CALLS': str(self.calls),
                    'AGTERM_UI_ARTIFACT_DIR': str(self.root / 'artifacts'), 'AGTERM_RESOURCE_ROOT': '/shared',
                    'AGTERM_ATSPI_SHARD_INDEX': '1', 'AGTERM_ATSPI_SHARD_COUNT': '2'}

    def execute(self, requested, **extra):
        with patch.dict(os.environ, {**self.env, **extra}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = targeted.main([requested, '--root', str(self.root)])
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, calls

    def test_each_selected_scenario_runs_alone_with_its_own_artifacts(self):
        result, calls = self.execute('gamma,alpha')
        self.assertEqual(result, 0)
        self.assertEqual([call['AGTERM_ATSPI_SCENARIO'] for call in calls], ['gamma', 'alpha'])
        for call in calls:
            self.assertEqual(call['AGTERM_UI_ARTIFACT_DIR'],
                             str(self.root / 'artifacts' / call['AGTERM_ATSPI_SCENARIO']))
            self.assertIsNone(call['AGTERM_RESOURCE_ROOT'])
            self.assertIsNone(call['AGTERM_ATSPI_SHARD_INDEX'])
            self.assertIsNone(call['AGTERM_ATSPI_SHARD_COUNT'])

    def test_a_failure_does_not_skip_the_rest_and_fails_the_run(self):
        result, calls = self.execute('alpha,beta,gamma', FAIL_SCENARIO='alpha')
        self.assertEqual(result, 1)
        self.assertEqual([call['AGTERM_ATSPI_SCENARIO'] for call in calls], ['alpha', 'beta', 'gamma'])

    def test_names_come_from_the_target_checkout(self):
        self.assertEqual(self.execute('normal'), (2, []))


class WorkflowTests(unittest.TestCase):
    """The two arch jobs must run the same steps, and the arch input must start exactly the chosen ones."""

    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/atspi-targeted.yml').read_text()

    def steps(self, job):
        body = self.workflow.split(f'\n  {job}:\n', 1)[1]
        body = re.split(r'\n  [a-z0-9_-]+:\n', body, maxsplit=1)[0]
        return body.split('\n    steps:\n', 1)[1]

    def test_arch_jobs_share_identical_steps(self):
        x86_64, aarch64 = self.steps('atspi-x86_64'), self.steps('atspi-aarch64')
        self.assertIn('GTK AT-SPI targeted scenarios', x86_64)
        self.assertEqual(x86_64, aarch64)

    def test_arch_input_selects_exactly_the_chosen_jobs(self):
        case = re.search(r'\n( *case "\$ARCH" in\n.*?\n *esac\n)', self.workflow, re.DOTALL).group(1)
        script = case + 'printf "%s %s" "$x86_64" "$aarch64"'
        for arch, expected in (('x86_64', 'true false'), ('aarch64', 'false true'), ('both', 'true true')):
            with self.subTest(arch=arch):
                result = subprocess.run(['bash', '-euc', script], env={'ARCH': arch}, capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, expected))
        result = subprocess.run(['bash', '-euc', script], env={'ARCH': 'riscv64'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        for job, arch in (('atspi-x86_64', 'x86_64'), ('atspi-aarch64', 'aarch64')):
            header = self.workflow.split(f'\n  {job}:\n', 1)[1].split('\n    steps:\n', 1)[0]
            self.assertIn(f"if: needs.select.outputs.{arch} == 'true'", header)
            self.assertIn(f'agterm-omarchy-build-{arch}@sha256:', header)


if __name__ == '__main__':
    unittest.main()
