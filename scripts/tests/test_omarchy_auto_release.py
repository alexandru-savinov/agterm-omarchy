#!/usr/bin/env python3
"""Offline tests for the automatic release workflow and incident actions."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "omarchy-auto-release.py"
SPEC = importlib.util.spec_from_file_location("auto_release", SCRIPT)
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
SHA = "a" * 40
TAG = "omarchy-v0.43.0.2"
CANDIDATE = {"run_id": "123", "sha": SHA, "attempt": "2", "version": "0.43.0.2"}
ARCHIVES = {arch: {"id": index, "name": f"agterm-omarchy-{arch}-{SHA}",
                   "filename": f"agterm-omarchy-{arch}.tar.gz", "transport_digest": "sha256:" + "b" * 64,
                   "sha256": "c" * 64, "attestation": "verified"}
            for index, arch in enumerate(auto.promotion.ARCHITECTURES, start=1)}
NATIVE_GATE = {"passed": "true", "attested": "true", "source_commit": SHA, "source_run_id": "123",
               "source_run_attempt": "2", "archives": json.dumps(ARCHIVES)}
EVENT = {"workflow_run": {"event": "push", "head_branch": "main", "conclusion": "success",
                          "head_repository": {"full_name": auto.promotion.REPOSITORY},
                          "id": 123, "head_sha": SHA, "run_attempt": 2}}
RELEASE = {"id": 456, "tag_name": TAG, "draft": False, "prerelease": False,
           "published_at": "2026-11-19T17:00:00Z"}
OPEN_INCIDENTS = "issues?labels=release-incident&state=open&per_page=100"


class PlanTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"SYNC_PAUSED": "", "GITHUB_RUN_ID": "456"})
        env.start()
        self.addCleanup(env.stop)

    def test_next_version_is_numeric_and_reserves_retracted_and_orphan_tags(self):
        self.assertEqual(auto.next_version("0.43.0", []), "0.43.0.1")
        self.assertEqual(auto.next_version("0.43.0", ["omarchy-v0.43.0.9", "omarchy-v0.43.0.10",
                         "omarchy-v0.43.0.0", "omarchy-v0.43.0.99-rc", "omarchy-v0.43.0.099",
                         "omarchy-v0.43.1.42", "omarchy-v0.43.0.11.99"]), "0.43.0.11")

    def test_pause_blocks_before_any_api_call(self):
        for value in ("true", " TRUE ", "1", "typo"):
            with self.subTest(value=value), patch.dict(os.environ, {"SYNC_PAUSED": value}), \
                 patch.object(auto.promotion, "api") as api:
                self.assertEqual(auto.plan(EVENT), (None, "SYNC_PAUSED is set"))
                api.assert_not_called()
        for value in ("", "   ", " FaLsE "):
            with self.subTest(value=value), patch.dict(os.environ, {"SYNC_PAUSED": value}), \
                 patch.object(auto.promotion, "api", return_value=[[]]):
                self.assertEqual(auto.pause_reason(), "")

    def test_manual_pr_and_failed_runs_do_not_plan(self):
        inputs = ({"event": "workflow_dispatch"}, {"event": "pull_request"}, {"head_branch": "feature"},
                  {"conclusion": "failure"}, {"head_repository": {"full_name": "other/fork"}})
        for changes in inputs:
            event = {"workflow_run": EVENT["workflow_run"] | changes}
            with self.subTest(changes=changes), patch.object(auto.promotion, "api") as api:
                self.assertIsNone(auto.plan(event)[0])
                api.assert_not_called()

    def test_superseded_commit_and_ci_attempt_do_not_plan(self):
        with patch.object(auto.promotion, "api", side_effect=[[[]], {"object": {"sha": "b" * 40}}]):
            self.assertIsNone(auto.plan(EVENT)[0])
        with patch.object(auto.promotion, "api", side_effect=[
            [[]], {"object": {"sha": SHA}}, {"run_attempt": 3, "status": "completed"}
        ]):
            self.assertIsNone(auto.plan(EVENT)[0])

    def test_candidate_uses_source_base_and_all_reserved_versions(self):
        run = EVENT["workflow_run"] | {
            "status": "completed", "name": "Omarchy CI", "workflow_id": 4, "path": auto.promotion.WORKFLOW,
            "repository": {"full_name": auto.promotion.REPOSITORY},
            "head_repository": {"full_name": auto.promotion.REPOSITORY},
        }
        fixtures = {"git/ref/heads/main": {"object": {"sha": SHA}}, "actions/runs/123": run,
                    OPEN_INCIDENTS: [[]],
                    "actions/workflows/ci.yml": {"id": 4},
                    auto.RELEASES_PATH: [[RELEASE | {"prerelease": True}]],
                    f"compare/{TAG}...{SHA}": {"status": "ahead"},
                    "git/matching-refs/tags/omarchy-v?per_page=100": [[{"ref": "refs/tags/omarchy-v0.43.0.10"}]]}
        run_filters = f"{auto.PROMOTION_RUNS}?branch=main&event=workflow_dispatch"
        fixtures.update({f"{run_filters}&status={status}&per_page=1": {"total_count": 0}
                         for status in auto.ACTIVE_PROMOTION_STATUSES})

        def api(path, **kwargs):
            return fixtures[path]

        with patch.object(auto.promotion, "api", side_effect=api), \
             patch.object(auto.promotion, "source_base", return_value="0.43.0") as source_base:
            self.assertEqual(auto.plan(EVENT)[0], CANDIDATE | {"version": "0.43.0.11"})
            self.assertEqual(source_base.call_args.args[1], SHA)
            fixtures[f"{run_filters}&status=queued&per_page=1"]["total_count"] = 1
            with patch.object(auto, "next_version") as serial:
                self.assertEqual(auto.plan(EVENT), (None, "A promotion workflow is already active"))
                serial.assert_not_called()
            fixtures[f"{run_filters}&status=queued&per_page=1"]["total_count"] = 0
            fixtures[f"compare/{TAG}...{SHA}"]["status"] = "identical"
            self.assertIsNone(auto.plan(EVENT)[0])
            source_base.return_value = "0.41.1"
            self.assertIsNone(auto.plan(EVENT)[0])

    def test_owner_pause_can_arrive_after_the_native_gate(self):
        with patch.dict(os.environ, {"SYNC_PAUSED": "true"}), patch.object(auto, "gh") as gh:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(auto.dispatch(CANDIDATE, NATIVE_GATE), "")
            gh.assert_not_called()

    def test_incident_pauses_selection_dispatch_and_final_check_until_closed(self):
        open_issue = {"number": 12}
        labelled_pr = {"number": 13, "pull_request": {}}
        with patch.object(auto.promotion, "api", return_value=[[labelled_pr], [open_issue]]) as api, \
             patch.object(auto, "gh") as gh, redirect_stdout(io.StringIO()):
            reason = auto.plan(EVENT)[1]
            self.assertIn("open release-incident", reason)
            self.assertEqual(auto.dispatch(CANDIDATE, NATIVE_GATE), "")
            gh.assert_not_called()
            with patch.object(sys, "argv", ["auto-release", "check-pause"]):
                with self.assertRaisesRegex(ValueError, "publication blocked"):
                    auto.main()
                api.return_value = [[labelled_pr]]
                auto.main()
            self.assertEqual(auto.pause_reason(), "")
            self.assertTrue(all(call.args == (OPEN_INCIDENTS,) and call.kwargs == {"pages": True}
                                for call in api.call_args_list))

    def test_dispatch_passes_the_gated_run_attempt_and_archive_evidence(self):
        with patch.object(auto.promotion, "api", return_value=[[]]), \
             patch.object(auto, "gh", return_value={"workflow_run_id": 99}) as gh, \
             patch.object(auto, "wait_for_promotion", return_value=TAG) as wait, redirect_stdout(io.StringIO()):
            self.assertEqual(auto.dispatch(CANDIDATE, NATIVE_GATE), TAG)
        gh.assert_called_once_with("actions/workflows/prerelease-omarchy.yml/dispatches", method="POST", data={
            "ref": "main", "inputs": {"source_run": "123", "source_sha": SHA, "source_attempt": "2",
                                      "native_archives": json.dumps(ARCHIVES), "auto_release_run_id": "456",
                                      "version": "0.43.0.2", "dry_run": False},
        })
        wait.assert_called_once_with(99, CANDIDATE)

    def test_dispatch_rejects_unattested_wrong_or_incomplete_native_evidence(self):
        changes = ({"passed": "false"}, {"attested": "false"}, {"source_commit": "b" * 40},
                   {"source_run_id": "124"}, {"source_run_attempt": "1"}, {"release_tag": TAG},
                   {"archives": "{}"}, {"archives": "invalid JSON"})
        for change in changes:
            with self.subTest(change=change), patch.object(auto.promotion, "api", return_value=[[]]), \
                 patch.object(auto, "gh") as gh:
                with self.assertRaises(ValueError):
                    auto.dispatch(CANDIDATE, NATIVE_GATE | change)
                gh.assert_not_called()


class WatchTests(unittest.TestCase):
    def test_watch_only_includes_stable_releases_in_publication_window(self):
        published = datetime(2026, 11, 19, 17, tzinfo=timezone.utc)
        now = published + timedelta(hours=23, minutes=59)
        with patch.object(auto, "promoted_automatically", return_value=True) as inspect_release:
            self.assertEqual(auto.find_watch_tags([
                RELEASE, RELEASE | {"prerelease": True}, RELEASE | {"draft": True},
                RELEASE | {"published_at": None}, RELEASE | {"published_at": (now + timedelta(seconds=1)).isoformat()},
                RELEASE | {"published_at": (now - timedelta(hours=24)).isoformat()},
            ], now), [TAG])
            self.assertEqual(auto.find_watch_tags([RELEASE], published + timedelta(hours=24)), [])
            inspect_release.assert_called_once_with(RELEASE)

    def test_scheduled_watch_requires_the_automatic_promotion_record(self):
        release = RELEASE | {"assets": [{"id": 101, "name": "promotion.json", "state": "uploaded", "size": 200}]}
        for record, expected in (({"tag": TAG, "auto_release_run_id": 12}, True), ({"tag": TAG}, False),
                                 ({"tag": TAG, "auto_release_run_id": True}, False),
                                 ({"tag": "different", "auto_release_run_id": 12}, False)):
            with self.subTest(record=record), patch.object(auto.subprocess, "check_output",
                                                           return_value=json.dumps(record).encode()):
                self.assertEqual(auto.promoted_automatically(release), expected)

    def test_watch_retraction_truth_table(self):
        for verdict, actions in {"passed": ("keep", "retract", "report"),
                                 "failed": ("retract", "retract", "retract"),
                                 "missing": ("report", "report", "report")}.items():
            for urls, action in zip(("success", "failure", ""), actions):
                with self.subTest(verdict=verdict, urls=urls):
                    self.assertEqual(auto.watch_action(verdict, urls), action)

    def test_native_status_only_uses_conclusive_evidence_from_the_expected_tag(self):
        red = {"result": "failure", "outputs": {"release_tag": TAG, "attested": "true",
                                               "passed": "false", "gate_failed": "true"}}
        green = {"result": "success", "outputs": {"release_tag": TAG, "attested": "true", "passed": "true"}}
        for attempt in (red | {"result": "cancelled"}, {"result": "failure"}, red | {"outputs": None},
                        red | {"outputs": red["outputs"] | {"gate_failed": "false"}},
                        red | {"outputs": red["outputs"] | {"attested": "false"}},
                        red | {"outputs": red["outputs"] | {"release_tag": "different"}}):
            with self.subTest(attempt=attempt):
                self.assertEqual(auto.watch_verdict(TAG, {"initial": attempt}), "missing")
                self.assertEqual(auto.watch_verdict(TAG, {"initial": attempt, "retry": green}), "passed")
        self.assertEqual(auto.watch_verdict(TAG, {"initial": red}), "failed")

    def test_retract_opens_labelled_incident_then_marks_prerelease(self):
        def read(path, **kwargs):
            if path == f"releases/tags/{TAG}":
                return RELEASE
            self.assertTrue(path.startswith("issues?labels=release-incident"))
            return [[]]

        with patch.object(auto.promotion, "api", side_effect=read), \
             patch.object(auto.promotion, "absent", return_value=True), \
             patch.object(auto, "gh", return_value={"html_url": "fixture issue"}) as gh, redirect_stdout(io.StringIO()):
            auto.retract(TAG, "native gates failed", "fixture run")
        self.assertEqual([(call.args[0], call.kwargs["method"]) for call in gh.call_args_list],
                         [("labels", "POST"), ("issues", "POST"), ("releases/456", "PATCH")])
        self.assertEqual(gh.call_args_list[2].kwargs["data"], {"prerelease": True, "make_latest": "false"})
        issue = gh.call_args_list[1].kwargs["data"]
        self.assertEqual(issue["labels"], ["release-incident"])
        self.assertIn("native gates failed", issue["body"])

    def test_retraction_attempts_both_actions_even_if_either_fails(self):
        for failing_action in ("record_incident", "mark_prerelease"):
            with self.subTest(failing_action=failing_action), patch.object(auto, "mark_prerelease") as mark, \
                 patch.object(auto, "record_incident") as incident:
                (incident if failing_action == "record_incident" else mark).side_effect = ValueError(failing_action)
                with self.assertRaisesRegex(ValueError, failing_action):
                    auto.retract(TAG, "failed", "fixture run")
                mark.assert_called_once_with(TAG)
                incident.assert_called_once_with(TAG, "failed", "fixture run")

    def test_already_retracted_release_and_incident_are_idempotent(self):
        with patch.object(auto.promotion, "api", side_effect=[
            RELEASE | {"prerelease": True}, [[{"title": f"Release incident: {TAG}", "state": "open"}]],
        ]), patch.object(auto, "gh") as gh:
            auto.mark_prerelease(TAG)
            auto.record_incident(TAG, "failed", "fixture run")
            gh.assert_not_called()

    def test_closed_incident_does_not_suppress_a_new_hold(self):
        def issues(path, **kwargs):
            closed = {"title": f"Release incident: {TAG}", "state": "closed"}
            return [[]] if path == OPEN_INCIDENTS else [[closed]]

        with patch.object(auto.promotion, "api", side_effect=issues), \
             patch.object(auto.promotion, "absent", return_value=False), \
             patch.object(auto, "gh", return_value={"html_url": "fixture"}) as gh, redirect_stdout(io.StringIO()):
            auto.record_incident(TAG, "again", "fixture")
        self.assertEqual(gh.call_args.kwargs["data"]["labels"], ["release-incident"])

    def test_missing_watch_evidence_uses_a_non_pausing_issue(self):
        with patch.object(auto, "record_issue") as issue, patch.object(auto, "mark_prerelease") as edit:
            auto.record_watch_unavailable(TAG, "fixture")
        self.assertEqual(issue.call_args.args[0], "release-watch-unavailable")
        edit.assert_not_called()

    def test_rehearsal_executes_the_real_retraction_with_stubbed_writes(self):
        result = subprocess.check_output([
            sys.executable, "-I", __file__, "--rehearse-retract",
        ], text=True, env={"PATH": os.defpath})
        writes = json.loads(result)
        self.assertEqual([(write["method"], write["path"]) for write in writes],
                         [("POST", "issues"), ("PATCH", "releases/456")])
        self.assertEqual(writes[0]["data"]["labels"], ["release-incident"])
        self.assertTrue(writes[1]["data"]["prerelease"])


def rehearse_retract():
    tag = "omarchy-v0.43.0.1"
    writes = []

    def stub_write(path, *, method, data):
        writes.append({"method": method, "path": path, "data": data})
        return {"html_url": "Synthetic incident"}

    with patch.object(auto.promotion, "api", side_effect=[[[]], RELEASE | {"tag_name": tag}]), \
         patch.object(auto.promotion, "absent", return_value=False), patch.object(auto, "gh", side_effect=stub_write), \
         redirect_stdout(io.StringIO()):
        auto.retract(tag, "Forced watch failure", "Synthetic release")
    print(json.dumps(writes, indent=2))


if __name__ == "__main__":
    if sys.argv[1:] == ["--rehearse-retract"]:
        rehearse_retract()
    else:
        unittest.main()
