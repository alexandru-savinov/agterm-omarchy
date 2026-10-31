#!/usr/bin/env python3
import importlib.util
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("pages_builder", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class PagesTests(unittest.TestCase):
    def roster(self):
        return json.loads(Path(__file__).with_name("roster.json").read_text())

    def test_roster_pages_and_json_preserve_sources_and_historical_states(self):
        roster = self.roster()
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, [], "a" * 40, roster=roster)
            root = Path(temporary)
            data = json.loads((root / "status.json").read_text())
            team = (root / "team.html").read_text()
            self.assertEqual(data["roster"]["observed_at"], roster["observed_at"])
            self.assertEqual(len(data["roster"]["agents"]), len(roster["agents"]))
            self.assertEqual(team.count('<li class="roster-card"'), len(roster["agents"]))
            for agent in data["roster"]["agents"]:
                self.assertIn(agent["source_url"], team)
                self.assertIn(agent["reported_at"], team)
                self.assertEqual(agent["task_url"], f'{build.BASE}/{agent["task_kind"]}/{agent["task"]}')
            self.assertIn('href="team.html"', (root / "index.html").read_text())
            self.assertIn("different payload", team)
            self.assertIn("SIGILL", team)
            self.assertIn("not proof of present liveness", team)
            self.assertIn("Completed report", team)
            self.assertIn('class="filters" hidden', team)
            self.assertIn('rel="icon" href="favicon.svg"', team)
            self.assertNotIn("$ agterm team --evidence", team)
            self.assertIn("A/B confirmed / renderer remedy under review", team)
            self.assertIn("not yet been persistently applied or accepted", team)
            self.assertIn("41030003211", team)
            self.assertIn("new CPU-fix artifact", team)
            self.assertIn("does not establish default-launch graphics compatibility", team)
            self.assertTrue((root / "favicon.svg").exists())
            self.assertIn("script-src 'self'", team)
            self.assertTrue((root / "team.js").exists())
            self.assertTrue((root / "team.css").exists())

    def test_roster_rejects_external_links_identity_collisions_and_bad_timestamps(self):
        for changes in ({"source": "https://evil.test/issues/1"},
                        {"source": "/issues/1#issuecomment-1\" onclick=\"x"},
                        {"source": "/issues/1/../../elsewhere"},
                        {"task": True}, {"reported_at": "2026-10-30"},
                        {"reported_at": "2099-10-30T00:00:00Z"},
                        {"phase": "online"}, {"phase": "assigned", "reported_state": "working"}):
            with self.subTest(changes=changes):
                data = self.roster()
                data["agents"][0].update(changes)
                with self.assertRaises(ValueError):
                    build.roster_snapshot(data)
        data = self.roster()
        data["agents"].append(copy.deepcopy(data["agents"][0]))
        with self.assertRaises(ValueError):
            build.roster_snapshot(data)

    def test_roster_text_is_escaped_and_join_block_is_canonical(self):
        data = self.roster()
        data["agents"][0]["work"] = '<img src=x onerror="alert(1)"> & hostile'
        data["agents"][0]["role"] = '<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, [], "a" * 40, roster=data)
            team = (Path(temporary) / "team.html").read_text()
            self.assertNotIn("<img src=x", team)
            self.assertNotIn("<script>alert", team)
            self.assertIn("&lt;img", team)
            self.assertIn("&lt;script&gt;", team)
            hub = (Path(temporary) / "index.html").read_text()
            self.assertIn("Agent: &lt;name&gt;\nTask: #&lt;number&gt;\nOwned paths: &lt;paths&gt;\nState: working\nConnected:", hub)
            self.assertNotIn("State: working /", hub)

    def test_team_landmarks_filters_and_asset_links_are_complete(self):
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids, self.refs, self.scripts = set(), [], []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if "id" in attrs:
                    self.ids.add(attrs["id"])
                if attrs.get("href", "").startswith("#"):
                    self.refs.append(attrs["href"][1:])
                if tag == "script":
                    self.scripts.append(attrs.get("src"))
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, [], "a" * 40)
            parser = Elements()
            parser.feed((Path(temporary) / "team.html").read_text())
            self.assertTrue(set(parser.refs) <= parser.ids)
            self.assertTrue({"search", "phase", "stream", "reset", "result-count", "no-results"} <= parser.ids)
            self.assertEqual(parser.scripts, ["team.js"])

    def test_untrusted_issue_content_is_escaped_and_urls_are_constructed(self):
        issue = {"number": 12, "title": '<script>alert("x")</script>&', "state": "open",
                 "html_url": "javascript:alert(1)", "body": "<img src=x onerror=alert(1)>"}
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, [[issue]], "a" * 40)
            html = (Path(temporary) / "index.html").read_text()
            self.assertNotIn("<script>", html)
            self.assertNotIn("javascript:", html)
            self.assertNotIn("onerror", html)
            self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;", html)
            self.assertIn(build.BASE + "/issues/12", html)

    def test_snapshot_retains_readiness_and_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, [], "0123456")
            root = Path(temporary)
            data = json.loads((root / "status.json").read_text())
            self.assertEqual(data["readiness"], "work-in-progress")
            self.assertEqual(data["upstream_baseline"], "v0.35.1")
            self.assertEqual(data["commit"], "0123456")
            self.assertTrue((root / "style.css").stat().st_size > 0)
            self.assertTrue((root / ".nojekyll").exists())
            self.assertIn("not a verified stable release", (root / "index.html").read_text())

    def test_issues_only_sorted_open_first_and_limited(self):
        issues = [{"number": n, "title": str(n), "state": "closed" if n == 1 else "open"}
                  for n in range(1, 16)]
        issues += [{"number": 16, "title": "PR", "state": "open", "pull_request": {}},
                   {"number": '1" onclick="alert(1)', "state": "open"},
                   {"number": True, "state": "open"}, {"number": 17, "state": "invalid"}]
        tasks = build.tasks_from_pages([issues], 10)
        self.assertEqual([task["number"] for task in tasks], list(range(2, 12)))

    def test_rejects_invalid_build_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            for commit in ('"><script>', "not-a-sha", ""):
                with self.assertRaises(ValueError):
                    build.write_site(temporary, [], commit)
            with self.assertRaises(ValueError):
                build.write_site(temporary, [], "123abcd", limit=999)
            with self.assertRaises(ValueError):
                build.write_site(temporary, {}, "123abcd")

    def queue(self, roster, observed_at=None, extra=True):
        from datetime import datetime, timezone
        first = roster["agents"][0]
        claims = [{"account": first["account"], "name": first["name"], "task": 999,
                   "latest": {"state": "working", "url": build.BASE + "/issues/999#issuecomment-1",
                              "created_at": "2026-10-30T01:00:00Z", "age_seconds": 36000, "stale": True}}]
        if extra:
            claims.append({"account": "shared", "name": "newcomer", "task": 7,
                           "latest": {"state": "ready-for-review", "url": build.BASE + "/issues/7#issuecomment-2",
                                      "created_at": "2026-10-30T02:00:00Z", "age_seconds": 100, "stale": False}})
        claims.append({"account": "shared", "name": "evil", "task": 8,
                       "latest": {"state": "working", "url": "https://evil.example/x",
                                  "created_at": "2026-10-30T03:00:00Z", "age_seconds": 1, "stale": False}})
        now = observed_at or datetime.now(timezone.utc).isoformat()
        return {"observed_at": now, "claims": claims}

    def build_team(self, queue):
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, [], "a" * 40, roster=self.roster(), queue=queue)
            return (Path(temporary) / "team.html").read_text(), json.loads((Path(temporary) / "status.json").read_text())

    def test_queue_overlays_newest_claim_and_marks_stale(self):
        roster = self.roster()
        team, data = self.build_team(self.queue(roster))
        self.assertIn(build.BASE + "/issues/999#issuecomment-1", team)
        self.assertIn("stale: no check-in for over 4h", team)
        self.assertEqual(team.count('<li class="roster-card"'), len(roster["agents"]))

    def test_queue_only_agent_is_listed_and_foreign_links_are_dropped(self):
        team, data = self.build_team(self.queue(self.roster()))
        self.assertIn('<li class="roster-card queue-only" data-phase="awaiting-review"', team)
        self.assertIn("newcomer", team)
        self.assertNotIn("evil", team)
        self.assertEqual([entry["name"] for entry in data["queue"]["queue_only"]], ["newcomer"])

    def test_older_stale_claim_stays_visible_behind_a_newer_claim(self):
        roster = self.roster()
        queue = self.queue(roster)
        first = roster["agents"][0]
        queue["claims"].append({"account": first["account"], "name": first["name"], "task": 1000,
                                "latest": {"state": "working", "url": build.BASE + "/issues/1000#issuecomment-3",
                                           "created_at": "2026-10-30T09:00:00Z", "age_seconds": 60, "stale": False}})
        team, data = self.build_team(queue)
        self.assertIn(build.BASE + "/issues/1000#issuecomment-3", team)
        self.assertIn('Also stale:</strong> <a href="' + build.BASE + '/issues/999#issuecomment-1">#999</a> (10h00m)', team)

    def test_newest_claim_uses_the_queue_ordering(self):
        roster = self.roster()
        queue = self.queue(roster)
        first = roster["agents"][0]
        # Older post, edited last: the queue's (updated_at, url) ordering makes it the latest.
        queue["claims"].append({"account": first["account"], "name": first["name"], "task": 1001,
                                "latest": {"state": "blocked", "url": build.BASE + "/issues/1001#issuecomment-4",
                                           "created_at": "2026-10-29T00:00:00Z", "updated_at": "2026-10-30T11:00:00Z",
                                           "age_seconds": 50000, "stale": False}})
        team, data = self.build_team(queue)
        self.assertIn('Queue: <a href="' + build.BASE + '/issues/1001#issuecomment-4">#1001</a>', team)

    def test_old_queue_snapshot_is_flagged(self):
        team, data = self.build_team(self.queue(self.roster(), observed_at="2026-01-01T00:00:00+00:00"))
        self.assertIn("Queue snapshot is", team)
        self.assertIsNotNone(data["queue"]["note"])

    def test_without_queue_the_page_is_unchanged(self):
        team, data = self.build_team(None)
        self.assertNotIn('class="queue', team)
        self.assertIsNone(data["queue"])


if __name__ == "__main__":
    unittest.main()
