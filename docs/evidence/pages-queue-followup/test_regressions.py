#!/usr/bin/env python3
"""Run against a checkout: python3 test_regressions.py /path/to/repo."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = Path(sys.argv.pop(1)).resolve()
SPEC = importlib.util.spec_from_file_location('builder', ROOT / 'docs/omarchy-pages/build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class RegressionTests(unittest.TestCase):
    def render(self, state='ready-for-review', queue=True):
        roster = json.loads((ROOT / 'docs/omarchy-pages/roster.json').read_text())
        roster['agents'] = [next(a for a in roster['agents'] if a['phase'] == 'completed')]
        agent = roster['agents'][0]
        now = datetime.now(timezone.utc).isoformat()
        def claim(account, name, task):
            return {'account': account, 'name': name, 'task': task, 'latest': {
                'state': state, 'url': build.BASE + f'/issues/{task}#issuecomment-123',
                'created_at': now, 'age_seconds': 0, 'stale': False}}
        snapshot = {'observed_at': now, 'claims': [claim(agent['account'], agent['name'], 75),
                    claim('external-contributor', 'external-review', 79)]} if queue else None
        with tempfile.TemporaryDirectory() as tmp:
            build.write_site(tmp, [], 'a' * 40, roster=roster, queue=snapshot)
            root = Path(tmp)
            return ((root/'team.html').read_text(), (root/'index.html').read_text(),
                    json.loads((root/'status.json').read_text()), agent)

    def test_existing_member_filter_tracks_each_queue_state(self):
        for state, phase in {'working':'working', 'blocked':'awaiting-review',
                             'ready-for-review':'awaiting-review', 'complete':'completed'}.items():
            with self.subTest(state=state):
                team, _, data, original = self.render(state)
                self.assertEqual(re.search(r'<li class="roster-card" data-phase="([^"]+)"', team)[1], phase)
                self.assertIn(f'<span class="phase">{build.PHASES[phase]}</span>', team)
                self.assertEqual(data['roster']['agents'][0]['phase'], original['phase'])
                self.assertEqual(data['roster']['agents'][0]['work'], original['work'])

    def test_totals_include_queue_only_cards(self):
        team, index, _, _ = self.render()
        count = len(re.findall(r'<li class="roster-card(?: queue-only)?"', team))
        self.assertEqual(count, 2)
        self.assertIn(f'{count} contributors in this snapshot', team)
        self.assertIn(f'<dt>named contributors</dt><dd>{count:02}</dd>', team)
        self.assertIn(f'{count} named contributors.', index)
        self.assertIn('<dt>review / decision handoffs</dt><dd>02</dd>', team)
        self.assertIn('<dt>completed reports</dt><dd>00</dd>', team)

    def test_external_account_does_not_contradict_footer(self):
        team, _, _, _ = self.render()
        self.assertIn('GitHub account: external-contributor', team)
        self.assertNotIn('All listed names use the shared', team)
        self.assertIn('independent approval', team)

    def test_absent_queue_preserves_curated_phase(self):
        team, _, data, original = self.render(queue=False)
        self.assertIn(f'data-phase="{original["phase"]}"', team)
        self.assertIsNone(data['queue'])
        self.assertIn('1 contributors in this snapshot', team)


if __name__ == '__main__':
    unittest.main()
