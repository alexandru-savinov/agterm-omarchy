#!/usr/bin/env python3
"""Offline ledger tests. The 500-task fixture is synthetic, never real agent activity."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import waves

REPO = 'alexandru-savinov/agterm-omarchy'
ISSUE = f'https://github.com/{REPO}/issues/120'
SOURCE = ISSUE + '#issuecomment-6561677455'
COMMIT = f'https://github.com/{REPO}/commit/' + 'a' * 40
CLI = Path(waves.__file__).resolve()
EXAMPLE = CLI.parents[2] / 'docs/wave-500-example.json'


def plan(count=4):
    return dict(schema='agterm-wave-plan/1', repository=REPO, requested=500, capacity=3,
                assignments=[dict(id=f'synthetic-{i}', title=f'Synthetic test assignment {i}', source=ISSUE,
                                  paths=[f'fixtures/task-{i}'], depends_on=[], max_attempts=2) for i in range(count)])


def event(operation, task=0, agent='worker', **extra):
    result = dict(event_id=f'{operation}-{task}-{agent}', operation=operation, task=f'synthetic-{task}',
                  actor=dict(account='example', agent=agent, session='test1234'), source=SOURCE)
    result.update(extra)
    return result


class WaveTests(unittest.TestCase):
    def setUp(self):
        self.queue = waves.initialize(plan())
        self.now = 1000

    def apply(self, request):
        self.queue = waves.transition(self.queue, request, self.queue['revision'], self.now)
        return self.queue['assignments'][request['task']]

    def claim(self, task=0, agent='worker'):
        return self.apply(event('claim', task, agent, lease_seconds=60))['lease']['token']

    def finish(self, task=0, agent='worker'):
        token = self.claim(task, agent)
        self.apply(event('start', task, agent, token=token))
        request = event('complete', task, agent, token=token, evidence=[COMMIT])
        self.apply(request)
        return request

    def test_requested_is_not_execution(self):
        result = waves.summary(self.queue, self.now)
        self.assertEqual((result['requested'], result['assigned'], result['unscoped_requested']), (500, 4, 496))
        self.assertEqual(result['reported_states'], dict(queued=4, claimed=0, running=0, blocked=0, completed=0, rejected=0))
        self.assertEqual(result['reserved_slots'], 0)

    def test_synthetic_500_bounded_batches(self):
        result = waves.dry_run(plan(500), 50)
        self.assertEqual(result['planned_batch_count'], 167)
        self.assertEqual(len(result['planned_batches']), 50)
        self.assertTrue(result['truncated'])
        self.assertFalse(result['execution_performed'])
        self.assertEqual(result['summary']['reported_states']['queued'], 500)
        self.assertTrue(all(len(batch) <= 3 for batch in result['planned_batches']))

    def test_plan_rejections(self):
        invalid = []
        for key, value in [('requested', 501), ('capacity', 4), ('capacity', True)]:
            candidate = plan(); candidate[key] = value; invalid.append(candidate)
        candidate = plan(501); invalid.append(candidate)
        candidate = plan(); candidate['assignments'][1]['id'] = 'synthetic-0'; invalid.append(candidate)
        for path in ['../escape', '/absolute', 'a/*', 'a/./b', 'a//b', 'x;touch pwned']:
            candidate = plan(); candidate['assignments'][0]['paths'] = [path]; invalid.append(candidate)
        candidate = plan(); candidate['assignments'][0]['depends_on'] = ['absent']; invalid.append(candidate)
        candidate = plan(); candidate['assignments'][0]['depends_on'] = ['synthetic-1']; candidate['assignments'][1]['depends_on'] = ['synthetic-0']; invalid.append(candidate)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                waves.initialize(candidate)

    def test_capacity_blocked_and_expired_still_reserved(self):
        token = self.claim()
        self.claim(1, 'second'); self.claim(2, 'third')
        self.now += 61
        self.apply(event('block', token=token, reason='Waiting for coordinated recovery'))
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.claim(3, 'fourth')
        result = waves.summary(self.queue, self.now)
        self.assertEqual(result['expired_lease_count'], 3)
        self.assertEqual(result['reserved_slots'], 3)
        self.assertEqual(result['reported_states']['blocked'], 1)

    def test_expiry_does_not_allow_renewal_or_completion(self):
        token = self.claim()
        self.apply(event('start', token=token))
        self.now += 61
        for request in [event('renew', token=token, lease_seconds=60), event('complete', token=token, evidence=[COMMIT])]:
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.apply(request)

    def test_coordinated_retry_fences_old_worker(self):
        old = self.claim()
        with self.assertRaisesRegex(ValueError, 'proof'):
            self.apply(event('retry', agent='lead', confirmed_stopped=False, reason='No stop confirmation'))
        self.apply(event('retry', agent='lead', confirmed_stopped=True, reason='Owner confirmed process stopped in source'))
        request = event('claim', agent='replacement', lease_seconds=60)
        new = self.apply(request)['lease']['token']
        self.assertNotEqual(old, new)
        with self.assertRaisesRegex(ValueError, 'foreign'):
            self.apply(event('start', token=old))
        self.apply(event('start', agent='replacement', token=new))
        with self.assertRaisesRegex(ValueError, 'retry limit'):
            self.apply(event('retry', agent='lead', event_id='retry-limit', confirmed_stopped=True, reason='Second attempt failed'))
        self.apply(event('reject', agent='lead', confirmed_stopped=True, reason='Retry budget exhausted'))
        self.assertEqual(waves.summary(self.queue, self.now)['reported_states']['rejected'], 1)

    def test_ancestor_and_casefold_ownership(self):
        candidate = plan()
        candidate['assignments'][0]['paths'] = ['Scripts']
        candidate['assignments'][1]['paths'] = ['scripts/github/waves.py']
        candidate['assignments'][2]['paths'] = ['scripts-other/file.py']
        self.queue = waves.initialize(candidate)
        self.claim()
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.claim(1, 'second')
        self.claim(2, 'third')

    def test_same_agent_session_collision(self):
        self.claim()
        for session in ['test1234', 'new5678']:
            request = event('claim', 1, lease_seconds=60)
            request['actor']['session'] = session
            with self.assertRaisesRegex(ValueError, 'collision'):
                self.apply(request)

    def test_dependencies_require_completion(self):
        candidate = plan(); candidate['assignments'][1]['depends_on'] = ['synthetic-0']
        self.queue = waves.initialize(candidate)
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.claim(1)
        self.finish()
        self.claim(1)

    def test_rejected_dependency_is_not_satisfied(self):
        candidate = plan(); candidate['assignments'][1]['depends_on'] = ['synthetic-0']
        self.queue = waves.initialize(candidate)
        self.apply(event('reject', confirmed_stopped=False, reason='Out of scope'))
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.claim(1)

    def test_idempotent_event_and_conflicting_replay(self):
        request = event('claim', lease_seconds=60)
        self.apply(request)
        self.assertEqual(waves.transition(self.queue, request, 0, self.now + 100), self.queue)
        changed = dict(request, lease_seconds=120)
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.apply(changed)

    def test_completion_is_idempotent_and_evidence_immutable(self):
        request = self.finish()
        repeated = dict(request, event_id='duplicate-completion')
        self.apply(repeated)
        self.assertEqual(waves.summary(self.queue, self.now)['reported_states']['completed'], 1)
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            self.apply(dict(request, event_id='altered-completion', evidence=[SOURCE]))

    def test_only_running_owned_work_can_complete(self):
        token = self.claim()
        with self.assertRaisesRegex(ValueError, 'running'):
            self.apply(event('complete', token=token, evidence=[COMMIT]))
        with self.assertRaisesRegex(ValueError, 'foreign'):
            self.apply(event('start', agent='other', token=token))

    def test_exact_evidence_and_event_validation(self):
        token = self.claim(); self.apply(event('start', token=token))
        for links in [[], ['https://example.com'], [ISSUE], [COMMIT + '?x=1'], [COMMIT, COMMIT], [{}]]:
            with self.subTest(links=links), self.assertRaises(ValueError):
                self.apply(event('complete', token=token, evidence=links))
        for change in [dict(operation=[]), dict(task=[]), dict(actor=dict(account=123, agent='worker', session='test1234')),
                       dict(source=ISSUE), dict(command='touch /tmp/should-never-run')]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.apply(dict(event('renew', token=token, lease_seconds=60), **change))

    def test_stale_revision_and_corrupt_records(self):
        with self.assertRaisesRegex(ValueError, 'stale revision'):
            waves.transition(self.queue, event('claim', lease_seconds=60), 1, self.now)
        self.finish()
        for key, value in [('completion', {}), ('attempt', 0), ('lease', {})]:
            candidate = deepcopy(self.queue); candidate['assignments']['synthetic-0'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                waves.validate_ledger(candidate)

    def test_renewal_before_expiry(self):
        token = self.claim()
        self.now += 30
        task = self.apply(event('renew', token=token, lease_seconds=120))
        self.assertEqual(task['lease']['expires_at'], 1150)

    def test_actual_task_dry_run_no_execution_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(CLI), 'dry-run', str(EXAMPLE)], cwd=directory,
                                    text=True, capture_output=True, check=True)
            output = json.loads(result.stdout)
            self.assertEqual(output['planned_batches'], [['ledger-engine'], ['ledger-tests'], ['operator-guide']])
            self.assertFalse(output['execution_performed'])
            self.assertEqual(output['summary']['reported_states']['queued'], 3)
            self.assertEqual(output['summary']['unscoped_requested'], 497)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_concurrent_cli_updates_use_revision_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ledger = root / 'ledger.json'
            waves.write_atomic(ledger, self.queue)
            processes = []
            for index in range(2):
                path = root / f'event-{index}.json'
                path.write_text(json.dumps(event('claim', index, f'worker-{index}', lease_seconds=60)))
                processes.append(subprocess.Popen([sys.executable, str(CLI), 'apply', str(ledger), str(path),
                                                    '--expected-revision', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            results = [process.communicate(timeout=10) for process in processes]
            self.assertEqual(sorted(p.returncode for p in processes), [0, 2])
            self.assertTrue(any('stale revision' in stderr for _, stderr in results))
            actual = waves.read_json(ledger); waves.validate_ledger(actual)
            self.assertEqual(actual['revision'], 1)
            self.assertEqual(len(waves.held(actual)), 1)

    def test_atomic_failure_preserves_previous_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.json'; waves.write_atomic(path, self.queue)
            before = path.read_bytes()
            with patch.object(waves.os, 'replace', side_effect=OSError('synthetic failure')):
                with self.assertRaises(OSError):
                    waves.write_atomic(path, {'changed': True})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.json'; path.write_text('{"capacity": 3, "capacity": 500}')
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                waves.read_json(path)

    def test_size_bound_preserves_readable_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.json'; waves.write_atomic(path, self.queue)
            before = path.read_bytes()
            with patch.object(waves, 'MAX_BYTES', 10):
                with self.assertRaisesRegex(ValueError, 'archive'):
                    waves.write_atomic(path, self.queue)
                with self.assertRaisesRegex(ValueError, 'input exceeds'):
                    waves.read_json(path)
            self.assertEqual(path.read_bytes(), before)

    def test_lock_wait_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(waves.fcntl, 'flock', side_effect=BlockingIOError), \
                    patch.object(waves.time, 'monotonic', side_effect=[0, 6]):
                with self.assertRaisesRegex(ValueError, 'writer busy'):
                    with waves.locked(Path(directory) / 'ledger.json'):
                        self.fail('Must not enter without the lock')


if __name__ == '__main__':
    unittest.main()
