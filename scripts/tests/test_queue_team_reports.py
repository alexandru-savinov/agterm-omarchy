#!/usr/bin/env python3
"""A lead's newest canonical size report applies across its active tasks (#178)."""
import datetime
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


GITHUB = Path(__file__).resolve().parents[1] / "github"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


queue = load("team_report_queue", GITHUB / "queue.py")
# Reuse the existing issue/comment fixtures without replacing Python's queue module for other tests.
with mock.patch.dict(sys.modules, {"queue": queue}):
    fixtures = load("team_report_fixtures", GITHUB / "test_queue.py")


class TeamReportTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 29, 12, tzinfo=datetime.timezone.utc)

    def report(self, task=1, state="working", team=8, order=1, account="owner"):
        comment = fixtures.ClaimHistoryTests().claim(task=task, state=state, order=order, account=account)
        if team is not None:
            comment["body"] += f"\nTeam: {team}"
        return comment

    def snapshot(self, reports):
        return queue.snapshot([fixtures.ClaimHistoryTests.issue(n) for n in (1, 2)], {1: reports}, self.NOW)

    def test_complete_other_task_updates_size_without_erasing_active_history(self):
        for team, expected in ((1, 1), (12, 12), (None, 1)):
            with self.subTest(team=team):
                reports = [self.report(), self.report(state="blocked", order=2),
                           self.report(task=2, state="complete", team=team, order=3)]
                result = self.snapshot(reports)
                self.assertEqual((result["headcount"], result["active_posters"]), (expected, 1))
                first, second = result["claims"]
                self.assertEqual([r["state"] for r in first["history"]], ["working", "blocked"])
                self.assertEqual(first["latest"]["team"], 8)
                self.assertEqual(second["latest"]["state"], "complete")
                self.assertEqual(result["tasks"][0]["observed_claims"], [first["latest"]])
                self.assertEqual(result["agents"], [second["latest"]])

    def test_new_size_does_not_reactivate_complete_or_stale_lead(self):
        for state in ("complete", "working"):
            with self.subTest(state=state):
                old = self.report(state=state)
                old["created_at"] = "2026-10-29T02:00:00Z"
                result = self.snapshot([old, self.report(task=2, state="complete", team=12, order=2)])
                self.assertEqual((result["headcount"], result["active_posters"]), (0, 0))

    def test_newer_invalid_or_unknown_task_report_cannot_change_size(self):
        for extra in (self.report(task=2, team=0, order=2), self.report(task=99, team=12, order=2)):
            with self.subTest(body=extra["body"]):
                result = self.snapshot([self.report(), extra])
                self.assertEqual((result["headcount"], result["active_posters"]), (8, 1))
                self.assertEqual(len(result["claims"]), 1)
                self.assertEqual(len(result["uncertain_observations"]), 1)
                self.assertEqual(result["agents"][0]["url"], extra["html_url"])

    def test_edited_stale_report_updates_size_but_keeps_its_stale_claim(self):
        edited = self.report(task=2, team=3, order=2)
        edited["created_at"] = "2026-10-29T02:00:00Z"
        result = self.snapshot([self.report(), edited])
        self.assertEqual((result["headcount"], result["active_posters"]), (3, 1))
        self.assertTrue(result["claims"][1]["latest"]["stale"])
        self.assertIn("Stale working claim", result["tasks"][1]["coordination"][0])

    def test_order_ties_and_account_namespaces_keep_existing_ordering(self):
        complete = self.report(task=2, state="complete", team=3, order=2)
        complete["updated_at"] = "2026-10-29T10:01:00Z"
        reports = [self.report(), complete, self.report(account="other", team=5)]
        forward = self.snapshot(reports)
        self.assertEqual((forward["headcount"], forward["active_posters"]), (8, 2))
        self.assertEqual(self.snapshot(list(reversed(reports))), forward)


if __name__ == "__main__":
    unittest.main()
