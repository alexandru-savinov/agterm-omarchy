#!/usr/bin/env python3
"""Regression checks for the advisory macOS pilot."""

import datetime as dt
import importlib.util
import json
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures/conditional-macos"
SCRIPT = ROOT / "scripts/github/conditional_macos.py"
SPEC = importlib.util.spec_from_file_location("conditional_macos", SCRIPT)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)

NOW = dt.datetime(2026, 11, 27, 12, tzinfo=dt.timezone.utc)
SHA = "a" * 40
TREE = "b" * 40
DOC_STATUS = json.loads((FIXTURES / "statuses-doc-example.json").read_text())[0]
STATUS = {
    **DOC_STATUS,
    "context": pilot.CONTEXT,
    "creator": {**DOC_STATUS["creator"], "login": pilot.OWNER},
    "state": "success",
    "created_at": "2026-11-27T12:00:00Z",
    "description": f"v1 tree={TREE} exit=0 sha256={'c' * 64}",
    "target_url": (f"https://github.com/{pilot.REPO}/commit/{SHA}#local-check="
                   + quote('{"macOS":"27.2","Xcode":"27.2","tools":"Swift"}', safe="")),
}


class ConditionalMacOS(unittest.TestCase):
    def decision(self, event="pull_request", sha=SHA, statuses=(STATUS,), refs=(), draw=1):
        def api(path, **_):
            if path == f"git/commits/{sha}":
                return {"tree": {"sha": TREE}}
            if path.startswith(f"commits/{sha}/statuses?per_page=100&page="):
                offset = (int(path.rsplit("=", 1)[1]) - 1) * 100
                return list(statuses[offset:offset + 100])
            if path == pilot.FLAG_QUERY:
                return list(refs)
            if path == f"contents/{pilot.FLAG_FILE}?ref={SHA}":
                return {"type": "file"}
            self.fail(f"unexpected API request {path}")

        return pilot.decide(event, sha, api, now=NOW, randbelow=lambda n: draw % n)

    def test_a_matching_local_pass_skips(self):
        self.assertEqual(self.decision(), "skip")

    def test_b_new_commit_has_no_status(self):
        statuses = json.loads((FIXTURES / "public-1c89b217-statuses.json").read_text())
        self.assertEqual(self.decision(sha="1c89b2177e4376ee323c5e90b80cb65017671ded",
                                       statuses=statuses), "hosted")

    def test_newest_context_wins_even_with_equal_timestamps(self):
        for newest in (
            {**STATUS, "state": "failure"},
            {**STATUS, "creator": DOC_STATUS["creator"]},
            {**STATUS, "creator": None},
        ):
            with self.subTest(newest=newest):
                self.assertEqual(self.decision(statuses=(DOC_STATUS, newest, STATUS)), "hosted")
        self.assertEqual(self.decision(statuses=(DOC_STATUS, STATUS)), "skip")

    def test_owner_status_can_follow_a_page_of_unrelated_contexts(self):
        self.assertEqual(self.decision(statuses=[DOC_STATUS] * 100 + [STATUS]), "skip")

    def test_c_wrong_tree_runs_hosted(self):
        stale = {**STATUS, "description": STATUS["description"].replace(TREE, "e" * 40)}
        self.assertEqual(self.decision(statuses=(stale,)), "hosted")

    def test_d_push_bypasses_the_api_and_runs_hosted(self):
        def unexpected(*args, **kwargs):
            self.fail(f"unexpected API request {args}")

        self.assertEqual(pilot.decide("push", SHA, unexpected, now=NOW), "hosted")

    def test_e_sampled_pass_runs_hosted_and_flag_disables(self):
        self.assertEqual(self.decision(draw=0), "sample")
        self.assertEqual(self.decision(refs=({"ref": pilot.FLAG_REF, "object": {"sha": SHA}},)), "hosted")

    def test_fresh_owner_success_with_complete_payload(self):
        variants = [
            {"creator": {"login": "other"}},
            {"created_at": (NOW - dt.timedelta(hours=24)).isoformat()},
            {"created_at": (NOW + dt.timedelta(seconds=1)).isoformat()},
            {"state": "failure"},
            {"description": STATUS["description"].replace("exit=0", "exit=1")},
            {"target_url": "https://example.com"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(self.decision(statuses=({**STATUS, **variant},)), "hosted")


if __name__ == "__main__":
    unittest.main()
