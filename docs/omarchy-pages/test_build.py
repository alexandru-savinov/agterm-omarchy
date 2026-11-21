#!/usr/bin/env python3
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("pages_builder", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def built_site(commit="a" * 40, **kwargs):
    temporary = tempfile.TemporaryDirectory()
    build.write_site(temporary.name, commit, **kwargs)
    return temporary, Path(temporary.name)


class LandingPageTests(unittest.TestCase):
    def setUp(self):
        self.temporary, self.root = built_site()
        self.addCleanup(self.temporary.cleanup)
        self.index = (self.root / "index.html").read_text()

    def test_the_site_is_one_landing_page_with_its_assets(self):
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         [".nojekyll", "favicon.svg", "index.html", "status.json", "style.css", "team.html"])
        self.assertTrue((self.root / "style.css").stat().st_size > 0)
        self.assertIn('rel="icon" href="favicon.svg"', self.index)
        self.assertNotIn("<script", self.index)

    def test_it_says_what_it_is_and_links_release_install_status_and_discussions(self):
        self.assertIn("<h1>agterm for Omarchy</h1>", self.index)
        for target in ("/releases/latest", "#install", "/issues/525", "/discussions",
                       "/issues/new?template=bug.yml", "/blob/main/CONTRIBUTING.md", "/blob/main/SECURITY.md"):
            self.assertIn(f'href="{build.BASE}{target}"', self.index, target)
        self.assertIn("Status and roadmap", self.index)

    def test_no_team_room_hub_or_agent_roster_remains(self):
        for pattern in (r"/issues/1(?!\d)", r"/pull/6(?!\d)", r"team\.(html|css|js)", r"(?i)team room",
                        r"(?i)roster", r"(?i)check-in", r"Agent: ", r"(?i)contributors", r'id="(join|tasks|team)"',
                        r"(?i)500 agents", r"(?i)orchestration"):
            self.assertIsNone(re.search(pattern, self.index), pattern)
        status = json.loads((self.root / "status.json").read_text())
        self.assertEqual((status["tasks"], status["roster"]["agents"], status["queue"], status["queue_status"]),
                         ([], [], None, "not-requested"))

    def test_the_old_team_room_url_redirects_to_the_landing_page(self):
        team = (self.root / "team.html").read_text()
        self.assertIn('<meta http-equiv="refresh" content="0; url=./">', team)
        self.assertIn('<a href="./">', team)
        for pattern in (r"/issues/", r"(?i)roster", r"(?i)team room", r"<script"):
            self.assertIsNone(re.search(pattern, team), pattern)

    def test_release_status_describes_the_gates_and_links_the_mechanics(self):
        current = self.index.split('id="releases"', 1)[1].split("</section>", 1)[0]
        for target in ("releases/latest", "releases", "#install", "blob/main/packaging/linux/prerelease-omarchy.md"):
            self.assertIn(build.BASE + ("" if target.startswith("#") else "/") + target, current)
        for text in ("both architectures", "build-provenance attestations", "marks the release as a prerelease",
                     "hardware GPU rendering"):
            self.assertIn(text, current)
        self.assertLess(self.index.index('id="releases"'), self.index.index('id="help"'))

    def test_snapshot_keeps_the_fields_the_deploy_step_asserts(self):
        temporary, root = built_site("0123456")
        self.addCleanup(temporary.cleanup)
        data = json.loads((root / "status.json").read_text())
        self.assertEqual((data["repository"], data["commit"], data["readiness"]),
                         (build.REPOSITORY, "0123456", "released"))
        self.assertEqual(data["releases"], build.BASE + "/releases")
        self.assertEqual(data["status_issue"], build.BASE + "/issues/525")
        self.assertEqual(data["upstream_baseline"], "v0.43.0")
        self.assertNotIn("v0.35.1", (root / "index.html").read_text())

    def test_rejects_invalid_commits(self):
        with tempfile.TemporaryDirectory() as temporary:
            for commit in ('"><script>', "not-a-sha", ""):
                with self.assertRaises(ValueError):
                    build.write_site(temporary, commit)

    def test_cli_builds_the_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "site"
            subprocess.run([sys.executable, "-B", build.__file__, "--output", str(output), "--commit", "a" * 40],
                           check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(json.loads((output / "status.json").read_text())["upstream_drift_status"], "not-requested")


class UpstreamDriftTests(unittest.TestCase):
    """Hub drift block (#380, B4): numbers when fresh, a visible notice when stale or missing."""
    NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

    def run_entry(self, created="2026-11-09T05:17:00Z", run_id=43000000001):
        return {"databaseId": run_id, "createdAt": created, "url": "https://evil.example/run",
                "event": "schedule", "headBranch": "main"}

    def test_only_scheduled_or_dispatched_main_runs_are_selected(self):
        scheduled = self.run_entry("2026-11-09T05:17:00Z", 1)
        dispatched = {**self.run_entry("2026-11-10T05:17:00Z", 2), "event": "workflow_dispatch"}
        fork_pr = {**self.run_entry("2026-11-11T05:17:00Z", 3), "event": "pull_request"}
        other_branch = {**self.run_entry("2026-11-11T06:17:00Z", 4), "headBranch": "feature"}
        self.assertEqual(build.latest_drift_run([scheduled, fork_pr, dispatched, other_branch])["databaseId"], 2)
        self.assertEqual(build.latest_drift_run([scheduled])["databaseId"], 1)
        for runs in ([fork_pr, other_branch], [], None, {"databaseId": 1}, ["x"]):
            with self.subTest(runs=runs):
                self.assertIsNone(build.latest_drift_run(runs))

    def drift(self, **changes):
        return {"main": "a" * 40, "upstream": "b" * 40, "ahead": 12, "behind": 34,
                "conflicting_paths": ["README.md", "<script>x</script>"], "conflict_count": 2, **changes}

    def build_index(self, drift):
        with tempfile.TemporaryDirectory() as temporary:
            build.write_site(temporary, "a" * 40, drift=drift)
            root = Path(temporary)
            return (root / "index.html").read_text(), json.loads((root / "status.json").read_text())

    def test_fresh_snapshot_shows_ahead_behind_conflicts_and_constructed_run_link(self):
        snapshot = build.drift_snapshot(self.run_entry(), self.drift(), self.NOW)
        self.assertEqual(snapshot["status"], "loaded")
        index, data = self.build_index(snapshot)
        self.assertIn("<strong>12 commits ahead</strong>", index)
        self.assertIn("<strong>34 behind</strong>", index)
        self.assertIn("<strong>2 paths</strong>", index)
        self.assertIn(f'href="{build.BASE}/actions/runs/43000000001"', index)
        self.assertNotIn("evil.example", index)
        # Path names are untrusted text; they stay in the linked run's artifact, never on the page or in status.json.
        self.assertNotIn("<script>", index)
        self.assertNotIn("README.md", index)
        self.assertNotIn("conflicting_paths", data["upstream_drift"])
        self.assertNotIn("drift-notice", index)
        self.assertEqual(data["upstream_drift_status"], "loaded")
        self.assertEqual((data["upstream_drift"]["ahead"], data["upstream_drift"]["behind"],
                          data["upstream_drift"]["conflict_count"]), (12, 34, 2))
        self.assertEqual(data["upstream_drift"]["run_url"], f"{build.BASE}/actions/runs/43000000001")

    def test_older_than_eight_days_is_flagged_but_keeps_numbers(self):
        for created, status in (("2026-11-04T12:00:00Z", "loaded"), ("2026-11-04T11:00:00Z", "stale"),
                                ("2026-10-01T00:00:00Z", "stale")):
            with self.subTest(created=created):
                snapshot = build.drift_snapshot(self.run_entry(created), self.drift(), self.NOW)
                self.assertEqual(snapshot["status"], status)
                index, data = self.build_index(snapshot)
                self.assertEqual(data["upstream_drift_status"], status)
                self.assertIn("<strong>12 commits ahead</strong>", index)
                self.assertEqual("Stale drift snapshot" in index, status == "stale")

    def test_missing_or_invalid_inputs_render_a_notice_and_no_numbers(self):
        cases = {
            "no run": (None, self.drift()),
            "bad run id": (self.run_entry(run_id="1"), self.drift()),
            "naive timestamp": (self.run_entry("2026-11-09T05:17:00"), self.drift()),
            "future run": (self.run_entry("2026-11-20T00:00:00Z"), self.drift()),
            "missing artifact": (self.run_entry(), None),
            "negative count": (self.run_entry(), self.drift(behind=-1)),
            "boolean count": (self.run_entry(), self.drift(ahead=True)),
            "string count": (self.run_entry(), self.drift(conflict_count="2")),
            "bad commit": (self.run_entry(), self.drift(main="HEAD")),
        }
        for name, (run, drift) in cases.items():
            with self.subTest(case=name):
                snapshot = build.drift_snapshot(run, drift, self.NOW)
                self.assertEqual(snapshot["status"], "unavailable")
                self.assertNotIn("ahead", snapshot)
                index, data = self.build_index(snapshot)
                self.assertIn("Drift snapshot unavailable:", index)
                self.assertNotIn("commits ahead", index)
                self.assertEqual(data["upstream_drift_status"], "unavailable")
                self.assertTrue(data["upstream_drift"]["reason"])

    def test_not_requested_renders_nothing(self):
        index, data = self.build_index(None)
        self.assertNotIn('id="upstream-drift"', index)
        self.assertEqual(data["upstream_drift_status"], "not-requested")
        self.assertIsNone(data["upstream_drift"])

    def run_main(self, files):
        import contextlib, io
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ["build.py", "--output", str(root / "site"), "--commit", "a" * 40]
            for flag, name, content in files:
                if content is not None:
                    (root / name).write_text(content)
                argv += [flag, str(root / name)]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                build.main()
            return (root / "site" / "index.html").read_text(), json.loads((root / "site" / "status.json").read_text())

    def test_main_reads_gh_run_list_output_and_artifact(self):
        fresh = datetime.now(timezone.utc).isoformat()
        index, data = self.run_main([("--drift-run", "run.json", json.dumps([self.run_entry(fresh)])),
                                     ("--drift", "drift.json", json.dumps(self.drift()))])
        self.assertEqual(data["upstream_drift_status"], "loaded")
        self.assertIn("<strong>12 commits ahead</strong>", index)
        for name, files in {
            "empty run list": [("--drift-run", "run.json", "[]"), ("--drift", "drift.json", json.dumps(self.drift()))],
            "artifact not downloaded": [("--drift-run", "run.json", json.dumps([self.run_entry(fresh)])),
                                        ("--drift", "drift.json", None)],
            "unreadable artifact": [("--drift-run", "run.json", json.dumps([self.run_entry(fresh)])),
                                    ("--drift", "drift.json", "{not json")],
        }.items():
            with self.subTest(case=name):
                index, data = self.run_main(files)
                self.assertEqual(data["upstream_drift_status"], "unavailable")
                self.assertIn("Drift snapshot unavailable:", index)


if __name__ == "__main__":
    unittest.main()
