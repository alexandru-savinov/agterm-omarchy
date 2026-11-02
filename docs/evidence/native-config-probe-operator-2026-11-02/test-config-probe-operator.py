"""Synthetic outer-operator checks. Never launches a process or contacts the VM."""
import importlib.util
import itertools
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('operator_runner', Path(__file__).with_name('native-run-template.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
APP = 'a' * 64


def row(pid, parent=1, name='python3', tagged=False):
    return dict(pid=pid, parent=parent, name=name, tagged=tagged, start=str(pid * 10),
                uid=os.getuid(), state='S')


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.stage = Path(self.temporary.name)
        (self.stage / 'config-probe.py').write_text('# inert fixture\n')
        self.protected = {1: row(1, name='agterm-linux')}
        self.probe_sha = runner.digest(self.stage / 'config-probe.py')

    def invoke(self):
        return runner.run_config_probe(self.stage, self.stage / 'payload', {}, self.protected, APP)

    def test_replay_rejection_preserves_every_existing_evidence_byte(self):
        for name in ('config-probe-attempt.json', 'config-probe-recovery.json', 'config-probe-operator.json'):
            (self.stage / name).write_text('original-' + name)
        before = {p.name: p.read_bytes() for p in self.stage.iterdir()}
        with patch.object(runner, 'processes', return_value=self.protected), patch.object(runner, 'signal_owned') as send:
            result = self.invoke()
        self.assertFalse(result['passed'])
        self.assertIn('previously used', result['errors'][0])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.stage.iterdir()})
        send.assert_not_called()

    def test_wrong_probe_identity_does_not_claim_or_launch(self):
        with patch.object(runner, 'processes', return_value=self.protected), \
                patch.dict(runner.ASSETS, {'config-probe.py': 'b' * 64}), \
                patch.object(runner.subprocess, 'Popen') as launch:
            result = self.invoke()
        self.assertFalse(result['passed'])
        self.assertIn('Wrong config probe bytes', result['errors'][0])
        self.assertEqual([p.name for p in self.stage.iterdir()], ['config-probe.py'])
        launch.assert_not_called()

    def scenario(self, *, observed=True, wrong_image=False, leftover=False, launch_failure=False,
                 with_cli=False, journal_failure=False, report_failure=False):
        state = {'launched': False, 'ended': False, 'recovered': False}
        child, first, second = row(22, tagged=True), row(23, 22, 'agterm-linux'), row(24, 22, 'agterm-linux')
        cli = row(25, 22, 'agtermctl')
        signals = []
        baseline = {**self.protected, 99: row(99, name='unrelated')}

        def processes(token=None):
            if not state['launched'] or state['recovered'] or (state['ended'] and not leftover):
                return baseline
            return {**baseline, 22: child, **({23: first, 24: second} if observed else {}),
                    **({25: cli} if with_cli else {})}

        class Process:
            pid, returncode = 22, None
            polls = 0

            def __init__(inner, *args, **kwargs):
                if launch_failure:
                    raise OSError('fixture launch failure')
                state['launched'] = True
                (self.stage / 'config-probe-result.json').write_text(json.dumps({'passed': True}))

            def poll(inner):
                inner.polls += 1
                if inner.polls == 1:
                    return None
                inner.returncode = 0
                state['ended'] = True
                return 0

        original_digest = runner.digest

        def digest(path):
            return ('b' * 64 if wrong_image else APP) if str(path).startswith('/proc/') else original_digest(path)

        def readlink(path):
            pid = int(Path(path).parts[2])
            return str(self.stage / 'payload/bin' / ('agterm-linux.bin' if pid in (23, 24) else 'agtermctl.bin'))

        original_json = runner.atomic_json

        def persist(path, value):
            if journal_failure and state['launched'] and Path(path).name == 'config-probe-recovery.json':
                raise OSError('fixture journal failure')
            if report_failure and Path(path).name == 'config-probe-operator.json':
                raise OSError('fixture report failure')
            return original_json(path, value)

        def send(value, sig):
            self.assertNotIn(value['pid'], baseline)
            signals.append((value['pid'], sig))
            state['recovered'] = True
            return True

        with patch.object(runner, 'processes', side_effect=processes), \
                patch.object(runner, 'process_record', side_effect=lambda pid: processes().get(pid)), \
                patch.dict(runner.ASSETS, {'config-probe.py': self.probe_sha}), \
                patch.object(runner.subprocess, 'Popen', Process), patch.object(runner, 'digest', side_effect=digest), \
                patch.object(runner.os, 'readlink', side_effect=readlink), \
                patch.object(runner, 'atomic_json', side_effect=persist), \
                patch.object(runner, 'signal_owned', side_effect=send), \
                patch.object(runner.time, 'sleep'), patch.object(runner.time, 'monotonic', side_effect=itertools.count()):
            result = self.invoke()
        self.assertTrue(result['preservation']['passed'])
        if not report_failure:
            self.assertEqual(json.loads((self.stage / 'config-probe-operator.json').read_text()), result)
        return result, signals

    def test_exact_two_observed_app_images_and_clean_exit_are_required(self):
        result, signals = self.scenario()
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['app_processes']), 2)
        self.assertEqual(signals, [])

    def test_child_success_without_observed_images_does_not_pass(self):
        result, signals = self.scenario(observed=False)
        self.assertFalse(result['passed'])
        self.assertIn('identities are incomplete or wrong', result['errors'][0])
        self.assertEqual(signals, [])

    def test_normal_control_client_is_tracked_but_is_not_a_frontend_image(self):
        result, signals = self.scenario(with_cli=True)
        self.assertTrue(result['passed'])
        self.assertEqual({item['process']['pid'] for item in result['app_processes']}, {23, 24})
        self.assertEqual(result['tracked_process_count'], 4)
        self.assertEqual(signals, [])

    def test_journal_failure_cannot_prevent_owned_process_recovery(self):
        result, signals = self.scenario(with_cli=True, journal_failure=True)
        self.assertFalse(result['passed'])
        self.assertTrue(any('fixture journal failure' in error for error in result['errors']))
        self.assertEqual({pid for pid, sig in signals}, {22, 23, 24, 25})
        self.assertTrue(all(sig == signal.SIGTERM for pid, sig in signals))
        self.assertEqual(result['remaining_owned_processes'], [])

    def test_final_report_failure_returns_failed_result_after_cleaning(self):
        result, signals = self.scenario(report_failure=True)
        self.assertFalse(result['passed'])
        self.assertTrue(any('fixture report failure' in error for error in result['errors']))
        self.assertEqual(result['remaining_owned_processes'], [])
        self.assertEqual(signals, [])

    def test_wrong_running_image_does_not_pass(self):
        result, _ = self.scenario(wrong_image=True)
        self.assertFalse(result['passed'])
        self.assertIn('identities are incomplete or wrong', result['errors'][0])

    def test_recovery_signals_only_owned_processes_and_disqualifies_result(self):
        result, signals = self.scenario(leftover=True)
        self.assertFalse(result['passed'])
        self.assertTrue(signals)
        self.assertTrue(all(pid in (22, 23, 24) and sig == signal.SIGTERM for pid, sig in signals))
        self.assertEqual(result['remaining_owned_processes'], [])

    def test_failed_launch_preserves_baseline_and_records_failure(self):
        result, signals = self.scenario(launch_failure=True)
        self.assertFalse(result['passed'])
        self.assertIn('fixture launch failure', result['errors'][0])
        self.assertEqual(signals, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
