import unittest
import urllib.error
from unittest import mock

import labels
from queue import snapshot


def claim(name, state, stamp="2026-11-15T10:00:00Z", verified=True, stale=False, url=None):
    return {"name": name, "state": state, "updated_at": stamp, "url": url or f"https://example.test/{name}{stamp}",
            "verified": verified, "stale": stale}


def task(number, claims, current=()):
    return {"number": number, "labels": list(current), "observed_claims": claims}


class DesiredTests(unittest.TestCase):
    def test_newest_state_and_live_agents(self):
        t = task(1, [claim("alpha", "working", "2026-11-15T09:00:00Z"),
                     claim("beta", "blocked", "2026-11-15T11:00:00Z")])
        self.assertEqual(labels.desired(t), {"state:blocked", "agent:alpha", "agent:beta"})

    def test_complete_and_stale_claims_do_not_name_an_owner(self):
        t = task(1, [claim("alpha", "complete", "2026-11-15T09:00:00Z"),
                     claim("beta", "working", "2026-11-15T08:00:00Z", stale=True),
                     claim("gamma", "ready-for-review", "2026-11-15T10:00:00Z")])
        self.assertEqual(labels.desired(t), {"state:ready-for-review", "agent:gamma"})

    def test_unverified_or_unknown_state_is_ignored_and_means_no_change(self):
        for claims in ([], [claim("x", "working", verified=False)], [claim("x", "done")], [claim("x", None)]):
            self.assertIsNone(labels.desired(task(1, claims)), claims)

    def test_hostile_or_oversized_names_never_become_labels(self):
        t = task(1, [claim("ok", "working"), claim("Bad Name", "working"), claim("a" * 45, "working"),
                     claim("evil:label", "working"), claim("", "working")])
        self.assertEqual(labels.desired(t), {"state:working", "agent:ok"})

    def test_timestamp_ties_break_by_url_deterministically(self):
        a = claim("a", "working", url="https://example.test/1")
        b = claim("b", "blocked", url="https://example.test/2")
        for order in ([a, b], [b, a]):
            self.assertIn("state:blocked", labels.desired(task(1, order)))


class PlanTests(unittest.TestCase):
    def test_adds_missing_removes_obsolete_keeps_foreign_labels(self):
        t = task(7, [claim("alpha", "blocked")], current=["bug", "state:working", "agent:old"])
        self.assertEqual(labels.plan(t), ({"state:blocked", "agent:alpha"}, {"state:working", "agent:old"}))

    def test_already_correct_is_a_noop(self):
        t = task(7, [claim("alpha", "working")], current=["bug", "state:working", "agent:alpha"])
        self.assertEqual(labels.plan(t), (set(), set()))
        self.assertEqual(labels.plan_all({"tasks": [t]}), {})

    def test_no_visible_claim_leaves_existing_labels_alone(self):
        t = task(7, [], current=["state:blocked", "agent:alpha"])
        self.assertEqual(labels.plan(t), (set(), set()))

    def test_queue_snapshot_feeds_the_plan_end_to_end(self):
        issue = {"number": 5, "title": "T", "state": "open", "html_url": "https://example.test/5",
                 "labels": [{"name": "state:working"}]}
        def comment(login, association, body, stamp):
            return {"body": body, "user": {"login": login}, "author_association": association,
                    "html_url": f"https://example.test/{stamp}", "updated_at": stamp, "created_at": stamp}
        good = "Agent: alpha\nTask: #5\nOwned paths: none\nState: blocked"
        forged = "Agent: mallory\nTask: #5\nOwned paths: none\nState: complete"
        data = snapshot([issue], {5: [comment("owner", "OWNER", good, "2026-11-15T10:00:00Z"),
                                      comment("stranger", "NONE", forged, "2026-11-15T11:00:00Z")]},
                        now=labels_now())
        self.assertEqual(labels.plan_all(data), {5: ({"state:blocked", "agent:alpha"}, {"state:working"})})


def labels_now():
    import datetime
    return datetime.datetime(2026, 11, 15, 11, 30, tzinfo=datetime.timezone.utc)


class ApplyTests(unittest.TestCase):
    def test_creates_labels_adds_and_removes_with_escaped_names(self):
        sent = []
        labels.apply("o/r", "t", {3: ({"state:blocked"}, {"agent:x"})},
                     send=lambda method, url, token, body=None: sent.append((method, url, body)))
        self.assertEqual(sent, [
            ("POST", "https://api.github.com/repos/o/r/labels", {"name": "state:blocked", "color": "1d76db"}),
            ("POST", "https://api.github.com/repos/o/r/issues/3/labels", {"labels": ["state:blocked"]}),
            ("DELETE", "https://api.github.com/repos/o/r/issues/3/labels/agent%3Ax", None)])

    def test_existing_label_and_already_removed_label_are_tolerated_but_other_errors_are_not(self):
        def failing(code):
            def send(method, url, token, body=None):
                if (method, code) in (("POST", 422), ("DELETE", 404)) and "issues" not in url or method == "DELETE":
                    raise urllib.error.HTTPError(url, code, "x", {}, None)
            return send
        labels.apply("o/r", "t", {3: ({"state:blocked"}, set())}, send=failing(422))
        labels.apply("o/r", "t", {3: (set(), {"agent:x"})}, send=failing(404))
        with self.assertRaises(urllib.error.HTTPError):
            labels.apply("o/r", "t", {3: (set(), {"agent:x"})}, send=failing(500))


if __name__ == "__main__":
    unittest.main()
