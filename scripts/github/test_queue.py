import unittest
from queue import checkin, fetch, markdown, snapshot


class QueueTests(unittest.TestCase):
    def test_latest_named_checkin_across_tasks_and_shared_identity(self):
        issues = [{"number": n, "title": "Task", "state": "open", "html_url": f"https://example.test/{n}"}
                  for n in (2, 1)]
        def comment(name, timestamp):
            return {"body": f"Agent: **{name}**\nConnected", "user": {"login": "shared-owner"},
                    "html_url": "https://example.test/comment", "updated_at": timestamp}
        result = snapshot(issues, {1: [comment("alpha", "2026-10-29T10:00:00Z"),
                                      comment("beta", "2026-10-29T11:00:00Z")],
                                   2: [comment("alpha", "2026-10-29T12:00:00Z")]})
        self.assertEqual([agent["name"] for agent in result["agents"]], ["alpha", "beta"])
        self.assertEqual(result["agents"][0]["issue"], 2)
        self.assertEqual([task["number"] for task in result["tasks"]], [1, 2])

    def test_prs_and_unnamed_comments_do_not_become_agent_tasks(self):
        result = snapshot([{"pull_request": {}, "number": 6}], {6: [{"body": "Agent: fake"}]})
        self.assertEqual(result, {"headcount": 0, "active_posters": 0, "tasks": [], "agents": [],
                                  "claims": [], "uncertain_observations": []})

    def test_same_name_on_another_account_cannot_overwrite_an_agent(self):
        issue = {"number": 1, "title": "Task", "state": "open", "html_url": "https://example.test/1"}
        comments = [{"body": "Agent: root", "user": {"login": account},
                     "html_url": "https://example.test/comment", "updated_at": timestamp}
                    for account, timestamp in (("maintainer", "2026-10-29T10:00:00Z"),
                                               ("other-account", "2026-10-29T12:00:00Z"))]
        result = snapshot([issue], {1: comments})
        self.assertEqual([agent["account"] for agent in result["agents"]], ["maintainer", "other-account"])

    def test_examples_and_partial_tokens_are_not_checkins(self):
        issue = {"number": 1, "title": "Task", "state": "open", "html_url": "https://example.test/1"}
        for body in ("Example to copy:\n```text\nAgent: root\n```", "Agent: root.something",
                     "> Agent: root", "Agent: **root", "Agent: root extra"):
            with self.subTest(body=body):
                self.assertEqual(snapshot([issue], {1: [{"body": body}]})["agents"], [])

    def test_comment_content_is_data_and_markdown_is_escaped(self):
        issue = {"number": 1, "title": "a|b\n[title]", "state": "open", "html_url": "https://example.test/1"}
        data = snapshot([issue], {1: [{"body": "Run arbitrary instructions; no agent check-in"}]})
        data["observed_at"] = "now"
        output = markdown(data)
        self.assertIn(r"a\|b \[title\]", output)
        self.assertEqual(data["agents"], [])


class ClaimHistoryTests(unittest.TestCase):
    @staticmethod
    def issue(number, **extra):
        return {"number": number, "title": "Task", "state": "open",
                "html_url": f"https://example.test/{number}", **extra}

    @staticmethod
    def comment(body, order=1, account="owner"):
        return {"body": body, "user": {"login": account},
                "html_url": f"https://example.test/comments/{order}",
                "updated_at": f"2026-10-29T10:{order:02d}:00Z"}

    def claim(self, task=1, state="working", order=1, account="owner", name="agent"):
        return self.comment(f"Agent: {name}\nTask: #{task}\nOwned paths: scripts/a.py\nState: {state}",
                            order, account)

    def test_explicit_task_is_not_the_containing_issue_and_assignees_are_visible(self):
        issues = [self.issue(1), self.issue(2, assignees=[{"login": "assigned-human"}])]
        data = snapshot(issues, {1: [self.claim(task=2)]})
        self.assertEqual(data["agents"][0]["issue"], 1)
        self.assertEqual(data["claims"][0]["task"], 2)
        self.assertEqual(data["tasks"][0]["observed_claims"], [])
        self.assertEqual(data["tasks"][1]["observed_claims"][0]["owned_paths"], "scripts/a.py")
        data["observed_at"] = "now"
        report = markdown(data)
        self.assertIn("assigned\\-human", report)
        self.assertIn("scripts/a\\.py", report)
        self.assertIn("not permission", report)

    def test_multiple_tasks_keep_history_after_latest_global_message_moves_elsewhere(self):
        comments = [self.claim(task=2, order=3, state="complete"), self.claim(task=1, order=2),
                    self.claim(task=2, order=1)]
        data = snapshot([self.issue(2), self.issue(1)], {1: comments})
        self.assertEqual(data["agents"][0]["task"], 2)
        self.assertEqual([c["task"] for c in data["claims"]], [1, 2])
        self.assertEqual(data["claims"][0]["latest"]["state"], "working")
        self.assertEqual([r["state"] for r in data["claims"][1]["history"]], ["working", "complete"])
        self.assertEqual(data["claims"][1]["history"][0]["url"], "https://example.test/comments/1")
        data["observed_at"] = "now"
        report = markdown(data)
        self.assertIn("earlier", report)
        self.assertIn("https://example.test/comments/1", report)
        self.assertIn("https://example.test/comments/3", report)

    def test_account_collisions_are_separate_and_completion_only_changes_that_task(self):
        comments = [self.claim(account="First", name="ROOT", order=1),
                    self.claim(account="other", name="root", order=2),
                    self.claim(task=2, account="first", name="root", state="complete", order=3)]
        data = snapshot([self.issue(1), self.issue(2)], {1: comments})
        self.assertEqual(len(data["agents"]), 2)
        self.assertEqual(len(data["claims"]), 3)
        self.assertIn("Potential conflict", data["tasks"][0]["coordination"][0])
        comments.append(self.claim(account="FIRST", name="root", state="complete", order=4))
        data = snapshot([self.issue(1), self.issue(2)], {1: comments})
        self.assertFalse(any("conflict" in note for note in data["tasks"][0]["coordination"]))
        self.assertEqual(len(data["claims"][0]["history"]), 2)

    def test_missing_malformed_and_duplicate_tasks_never_infer_claims(self):
        for task in ("", "Task: #0", "Task: #01", "Task: #1 #2", "Task: #1 trailing",
                     "Task: available", "Task #1", "Task: #1\nTask: #1", "Task: #1\nTask: #2",
                     "Task: #" + "9" * 5000):
            with self.subTest(task=task):
                body = f"Agent: agent\n{task}\nOwned paths: scripts/a.py\nState: working"
                data = snapshot([self.issue(1)], {1: [self.comment(body)]})
                self.assertEqual(data["claims"], [])
                self.assertEqual(len(data["uncertain_observations"]), 1)
                self.assertIsNone(data["agents"][0]["task"])
                self.assertTrue(data["agents"][0]["uncertainties"])

    def test_duplicate_or_missing_fields_remain_uncertain_and_unknown_task_is_not_created(self):
        for field, key in (("State: complete\nState: working", "state"),
                           ("Owned paths: one\nOwned paths: two", "owned_paths")):
            data = snapshot([self.issue(1)], {1: [self.comment(f"Agent: a\nTask: #1\n{field}")]})
            self.assertEqual(data["claims"], [])
            rejected = data["uncertain_observations"][0]
            self.assertIsNone(rejected[key])
            self.assertEqual(rejected["field_values"]["task"], ["#1"])
            self.assertTrue(any("duplicate" in note for note in rejected["uncertainties"]))
        data = snapshot([self.issue(1)], {1: [self.claim(task=99, state="available")]})
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["observed_claims"], [])
        self.assertEqual(data["claims"], [])
        self.assertIsNone(data["uncertain_observations"][0]["state"])
        data = snapshot([self.issue(1)], {1: [self.claim(task=99)]})
        self.assertEqual(data["claims"], [])
        self.assertIn("task: issue absent from this task snapshot", data["uncertain_observations"][0]["uncertainties"])

    def test_fields_in_fences_quotes_and_indented_examples_are_not_claims(self):
        body = ("\nAgent: **Alpha**\n```text\nTask: #99\nState: complete\n```\n"
                "~~~text\nOwned paths: other\n~~~\n> Task: #98\n    Task: #97\n"
                "Task: #1\nOwned paths: scripts/a.py\nState: blocked")
        parsed = checkin(body)
        self.assertEqual(parsed["name"], "alpha")
        self.assertIsNone(parsed["task"])
        self.assertIsNone(parsed["state"])
        self.assertTrue(parsed["uncertainties"])

    def test_markdown_examples_cannot_advance_or_hide_prior_task_observations(self):
        fields = "Task: #1\nOwned paths: scripts/a.py\nState: complete"
        examples = ["Agent: agent\n\n> Example only:\n" + fields,
                    "Agent: agent\n<!--\n" + fields + "\n-->",
                    "    Agent: agent\n" + fields,
                    "\tAgent: agent\n" + fields,
                    "Agent: agent\n- Example only:\n" + fields,
                    "Agent: agent\n<pre>\n" + fields + "\n</pre>",
                    "Agent: agent\n\n" + fields,
                    "Agent: agent\nTask: #1\nOwned paths: <!--\nState: complete\n-->",
                    "Agent: agent\nTask: #1\nOwned paths: `\nState: complete\n`",
                    "Agent: agent\nOwned paths: ![Example only\nTask: #1\nState: complete\n](https://example.test/x.png)",
                    "Agent: agent\nTask: #1\nOwned paths: ![Example only\nState: complete\n](https://example.test/x.png)",
                    "Agent: agent\nTask: #1\nOwned paths: scripts/<span hidden>\nState: complete\n</span>"]
        for body in examples:
            with self.subTest(body=body):
                comments = [self.claim(order=1), self.claim(order=2, account="other"),
                            self.comment(body, order=3)]
                data = snapshot([self.issue(1, state="closed")], {1: comments})
                claims = data["tasks"][0]["observed_claims"]
                self.assertEqual(len(claims), 2)
                self.assertTrue(all(record["state"] == "working" for record in claims))
                self.assertTrue(all(len(group["history"]) == 1 for group in data["claims"]))
                notes = data["tasks"][0]["coordination"]
                self.assertTrue(any("Potential conflict" in note for note in notes))
                self.assertTrue(any("Closed issue" in note for note in notes))

    def test_uncertain_blocks_never_replace_a_prior_certain_observation(self):
        canonical = "Agent: agent\nTask: #1\nOwned paths: scripts/a.py\nState: complete"
        malformed = [canonical.replace("State: complete", "State: complete\nState: working"),
                     canonical.replace("Owned paths: scripts/a.py\n", ""),
                     canonical.replace("Owned paths: scripts/a.py", "Owned paths: "),
                     canonical.replace("State: complete", "State: unknown"),
                     canonical.replace("Task: #1\n", "Task: #1\nTask: #1\n"),
                     canonical.replace("Task: #1\nOwned paths: scripts/a.py", "Owned paths: scripts/a.py\nTask: #1")]
        for body in malformed:
            with self.subTest(body=body):
                data = snapshot([self.issue(1)], {1: [self.claim(), self.comment(body, order=2)]})
                self.assertEqual(data["claims"][0]["latest"]["state"], "working")
                self.assertEqual(len(data["claims"][0]["history"]), 1)
                self.assertEqual(len(data["uncertain_observations"]), 1)
                self.assertTrue(data["uncertain_observations"][0]["uncertainties"])

    def test_path_list_grammar_accepts_literal_paths_only(self):
        for paths in ("scripts/a.py", ".github/workflows/agent-queue.yml, docs/AGENT_PROTOCOL.md", "docs/"):
            parsed = checkin(f"Agent: a\nTask: #1\nOwned paths: {paths}\nState: working")
            self.assertEqual(parsed["uncertainties"], [])
            self.assertEqual(parsed["owned_paths"], paths)
        for paths in ("![example", "[link", "a&NewLine;b", "scripts/*", "a`b", "<span>",
                      "a|b", "a\\b", "a\tb", "a\x00b", "a\x1bb", "a\u202eb", "a b", "__", "a/__init__.py"):
            with self.subTest(paths=paths):
                parsed = checkin(f"Agent: a\nTask: #1\nOwned paths: {paths}\nState: complete")
                self.assertIsNone(parsed["task"])
                self.assertTrue(parsed["uncertainties"])

    def test_canonical_spacing_and_newline_boundaries(self):
        canonical = "Agent: a\nTask: #1\nOwned paths: docs/a.md\nState: working"
        self.assertEqual(checkin(canonical.replace("\n", "\r\n"))["task"], 1)
        for body in (canonical.replace("Task:", "task:"), canonical.replace("#1", " #1"),
                     canonical.replace("a.md", "a.md "), canonical.replace("working", "working\t"),
                     canonical.replace("\n", "\v"), canonical.replace("\n", "\r"),
                     canonical.replace("\n", "\u2028"), canonical.replace("Agent: a", "Agent:\ta")):
            with self.subTest(body=body):
                parsed = checkin(body)
                self.assertTrue(parsed is None or parsed["task"] is None)

    def test_only_the_immediate_plain_field_block_is_interpreted(self):
        body = ("Agent: **Alpha**\nTask: #1\nOwned paths: scripts/a.py\nState: blocked\n"
                "Evidence: example below\nTask: #99\nOwned paths: example\nState: complete")
        parsed = checkin(body)
        self.assertEqual((parsed["name"], parsed["task"], parsed["state"]), ("alpha", 1, "blocked"))
        self.assertEqual(parsed["uncertainties"], [])

    def test_equal_timestamps_have_stable_source_order_and_closed_issue_discrepancy(self):
        first = self.claim(order=1)
        last = self.claim(order=2, state="blocked")
        last["updated_at"] = first["updated_at"]
        issues = [self.issue(1, state="closed")]
        forward = snapshot(issues, {1: [first, last]})
        reverse = snapshot(issues, {1: [last, first]})
        self.assertEqual(forward, reverse)
        self.assertEqual(forward["claims"][0]["latest"]["state"], "blocked")
        self.assertIn("Closed issue", forward["tasks"][0]["coordination"][0])

    def test_markdown_treats_all_fields_as_data_and_cannot_inject_links_or_html(self):
        hostile = '<script>x</script> | ![x](https://evil.test) `code` @someone'
        issue = self.issue(1, title=hostile, labels=[{"name": hostile}], assignees=[{"login": hostile}])
        record = self.comment(f"Agent: a\nTask: #1\nOwned paths: {hostile}\nState: working")
        record["html_url"] = "https://example.test/c) [injected](https://evil.test)"
        malformed = self.comment(f"Agent: a\nTask: {hostile}\nState: complete", order=2)
        malformed["html_url"] = "javascript:alert(1)"
        data = snapshot([issue], {1: [record, malformed]})
        data["observed_at"] = "now"
        output = markdown(data)
        self.assertNotIn("<script>", output)
        self.assertNotIn("![x]", output)
        self.assertNotIn("@someone", output)
        self.assertNotIn("](javascript:", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn(r"\|", output)
        self.assertIn("%29%20%5Binjected%5D%28", output)
        self.assertIn("source URL unavailable", output)

    def _age_snapshot(self, reports, now="2026-10-30T12:00:00+00:00"):
        import datetime
        issue = {"number": 7, "title": "Task", "state": "open", "html_url": "https://example.test/7"}
        comments = [{"body": f"Agent: {name}\nTask: #7\nOwned paths: docs/x.md\nState: {state}",
                     "user": {"login": "shared"}, "html_url": f"https://example.test/{name}",
                     "created_at": created, "updated_at": updated or created}
                    for name, state, created, updated in reports]
        return snapshot([issue], {7: comments}, datetime.datetime.fromisoformat(now))

    def test_working_claim_is_stale_only_after_four_hours(self):
        for created, expected in (("2026-10-30T08:01:00Z", False),  # 3h59
                                  ("2026-10-30T08:00:00Z", False),  # exactly 4h00
                                  ("2026-10-30T07:59:00Z", True)):  # 4h01
            task = self._age_snapshot([("alpha", "working", created, None)])["tasks"][0]
            self.assertEqual(task["observed_claims"][0]["stale"], expected, created)

    def test_waiting_states_show_age_but_are_never_stale(self):
        for state in ("blocked", "ready-for-review"):
            claim = self._age_snapshot([("alpha", state, "2026-10-30T02:00:00Z", None)])["tasks"][0]["observed_claims"][0]
            self.assertEqual(claim["age_seconds"], 10 * 3600)
            self.assertFalse(claim["stale"])

    def test_edit_does_not_reset_age(self):
        claim = self._age_snapshot([("alpha", "working", "2026-10-30T02:00:00Z",
                                     "2026-10-30T11:59:00Z")])["tasks"][0]["observed_claims"][0]
        self.assertTrue(claim["stale"])

    def test_stale_claim_does_not_conflict_with_a_fresh_takeover(self):
        task = self._age_snapshot([("alpha", "working", "2026-10-30T02:00:00Z", None),
                                   ("beta", "working", "2026-10-30T11:30:00Z", None)])["tasks"][0]
        notes = " ".join(task["coordination"])
        self.assertNotIn("Potential conflict", notes)
        self.assertIn("Stale working claim: shared/alpha (10h00m", notes)
        self.assertIn(r"\(10h00m, stale\)", markdown({**self._age_snapshot(
            [("alpha", "working", "2026-10-30T02:00:00Z", None)]), "observed_at": "x"}))

    def test_two_fresh_working_claims_still_conflict(self):
        task = self._age_snapshot([("alpha", "working", "2026-10-30T11:00:00Z", None),
                                   ("beta", "working", "2026-10-30T11:30:00Z", None)])["tasks"][0]
        self.assertIn("Potential conflict", " ".join(task["coordination"]))


class SessionCollisionTests(unittest.TestCase):
    NOW = "2026-10-30T12:00:00+00:00"

    def _snapshot(self, reports):
        """reports: (task, name, state, session or None, created_at)."""
        import datetime
        issues = [{"number": n, "title": "Task", "state": "open", "html_url": f"https://example.test/{n}"}
                  for n in (7, 8)]
        comments = {7: [], 8: []}
        for order, (task, name, state, session, created) in enumerate(reports):
            body = f"Agent: {name}\nTask: #{task}\nOwned paths: docs/x.md\nState: {state}"
            if session is not None:
                body += f"\nSession: {session}"
            comments[task].append({"body": body + "\nEvidence: none", "user": {"login": "shared"},
                                   "html_url": f"https://example.test/c{order}",
                                   "created_at": created, "updated_at": created})
        return snapshot(issues, comments, datetime.datetime.fromisoformat(self.NOW))

    def notes(self, result):
        return {task["number"]: " ".join(task["coordination"]) for task in result["tasks"]}

    def test_two_sessions_under_one_name_are_flagged_on_both_tasks(self):
        notes = self.notes(self._snapshot([(7, "codex", "working", "a1b2", "2026-10-30T11:00:00Z"),
                                           (8, "codex", "working", "c3d4", "2026-10-30T11:10:00Z")]))
        for number in (7, 8):
            self.assertIn("Name collision: shared/codex posted from 2 sessions", notes[number])

    def test_one_session_on_two_tasks_is_not_a_collision(self):
        notes = self.notes(self._snapshot([(7, "codex", "working", "a1b2", "2026-10-30T11:00:00Z"),
                                           (8, "codex", "blocked", "a1b2", "2026-10-30T11:10:00Z")]))
        self.assertNotIn("Name collision", " ".join(notes.values()))

    def test_checkins_without_session_never_collide_and_parse_as_before(self):
        result = self._snapshot([(7, "codex", "working", None, "2026-10-30T11:00:00Z"),
                                 (8, "codex", "working", "c3d4", "2026-10-30T11:10:00Z")])
        self.assertNotIn("Name collision", " ".join(self.notes(result).values()))
        self.assertEqual([claim["latest"]["uncertainties"] for claim in result["claims"]], [[], []])
        self.assertIsNone(result["claims"][0]["latest"]["session"])

    def test_stale_or_complete_old_session_is_not_a_collision(self):
        for task7 in ((7, "codex", "working", "a1b2", "2026-10-30T02:00:00Z"),
                      (7, "codex", "complete", "a1b2", "2026-10-30T11:00:00Z")):
            with self.subTest(task7=task7):
                notes = self.notes(self._snapshot([task7, (8, "codex", "working", "c3d4",
                                                           "2026-10-30T11:10:00Z")]))
                self.assertNotIn("Name collision", " ".join(notes.values()))

    def test_different_names_with_different_sessions_are_not_a_collision(self):
        notes = self.notes(self._snapshot([(7, "alpha", "working", "a1b2", "2026-10-30T11:00:00Z"),
                                           (8, "beta", "working", "c3d4", "2026-10-30T11:10:00Z")]))
        self.assertNotIn("Name collision", " ".join(notes.values()))

    def test_malformed_session_makes_the_block_uncertain(self):
        for session in ("AB12", "abc", "a" * 17, "a1-b2", ""):
            with self.subTest(session=session):
                result = self._snapshot([(7, "codex", "working", session, "2026-10-30T11:00:00Z")])
                self.assertEqual(result["claims"], [])
                self.assertEqual(len(result["uncertain_observations"]), 1)

    def test_session_must_follow_state(self):
        body = "Agent: codex\nTask: #7\nSession: a1b2\nOwned paths: docs/x.md\nState: working"
        self.assertIsNone(checkin(body)["task"])
        self.assertEqual(checkin(body + "\nSession: a1b2")["state"], None)


class FetchTests(unittest.TestCase):
    def test_comments_come_from_one_stream_grouped_by_issue(self):
        issues = [{"number": n, "title": "Task", "state": "open", "html_url": f"https://example.test/{n}",
                   "comments": 3} for n in range(1, 41)] + [{"number": 99, "pull_request": {}, "comments": 3}]
        stream = [{"issue_url": f"https://api.github.com/repos/o/r/issues/{n}", "id": n * 10 + k,
                   "body": "Agent: alpha\nTask: #1\nOwned paths: a.py\nState: working",
                   "user": {"login": "shared"}, "html_url": f"https://example.test/c{n}-{k}",
                   "updated_at": "2026-10-30T10:00:00Z"} for n in list(range(1, 41)) + [99] for k in range(3)]
        requests = []
        def fake_pages(repository, endpoint, token):
            requests.append(endpoint)
            if endpoint.startswith("issues?"):
                return issues
            self.assertEqual(endpoint, "issues/comments?sort=created&direction=asc")
            return stream
        got_issues, grouped = fetch("o/r", "t", fake_pages)
        self.assertEqual(len(requests), 2)  # never one request per issue
        self.assertEqual(sorted(grouped), list(range(1, 41)))  # PR #99 dropped
        self.assertTrue(all(len(grouped[n]) == 3 for n in grouped))
        self.assertEqual([c["id"] for c in grouped[7]], [70, 71, 72])  # order preserved
        self.assertEqual(len(snapshot(got_issues, grouped)["claims"]), 1)


class TeamTests(unittest.TestCase):
    def _snapshot(self, reports, now="2026-10-30T12:00:00+00:00"):
        """reports: (task, name, state, extra field lines, created_at)."""
        import datetime
        issues = [{"number": n, "title": "Task", "state": "open", "html_url": f"https://example.test/{n}"}
                  for n in (7, 8)]
        comments = {7: [], 8: []}
        for order, (task, name, state, extra, created) in enumerate(reports):
            body = f"Agent: {name}\nTask: #{task}\nOwned paths: docs/x.md\nState: {state}{extra}\nNext: x"
            comments[task].append({"body": body, "user": {"login": "shared"},
                                   "html_url": f"https://example.test/c{order}",
                                   "created_at": created, "updated_at": created})
        return snapshot(issues, comments, datetime.datetime.fromisoformat(now))

    def test_leads_report_team_size_and_headcount_sums_it(self):
        result = self._snapshot([(7, "lead-a", "working", "\nTeam: 12", "2026-10-30T11:00:00Z"),
                                 (8, "lead-b", "working", "\nTeam: 30\nSession: abcd", "2026-10-30T11:00:00Z"),
                                 (8, "solo", "blocked", "", "2026-10-30T11:00:00Z")])
        self.assertEqual((result["headcount"], result["active_posters"]), (43, 3))
        self.assertEqual(result["claims"][0]["latest"]["team"], 12)

    def test_stale_complete_and_malformed_teams_do_not_count(self):
        result = self._snapshot([(7, "old", "working", "\nTeam: 50", "2026-10-30T02:00:00Z"),
                                 (7, "done", "complete", "\nTeam: 50", "2026-10-30T11:00:00Z"),
                                 (8, "bad", "working", "\nTeam: 0", "2026-10-30T11:00:00Z"),
                                 (8, "big", "working", "\nTeam: 10000", "2026-10-30T11:00:00Z")])
        self.assertEqual((result["headcount"], result["active_posters"]), (0, 0))
        self.assertEqual(len(result["uncertain_observations"]), 2)

    def test_team_after_session_is_out_of_order(self):
        self.assertIsNone(checkin("Agent: a\nTask: #7\nOwned paths: x.md\nState: working\n"
                                  "Session: abcd\nTeam: 3")["task"])

    def test_fetch_passes_the_comment_window(self):
        import datetime
        seen = []
        def fake_pages(repository, endpoint, token):
            seen.append(endpoint)
            return []
        fetch("o/r", "t", fake_pages, since=datetime.datetime(2026, 10, 16, 12, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(seen[1], "issues/comments?sort=created&direction=asc&since=2026-10-16T12:00:00Z")

    def test_markdown_shows_headcount_and_window(self):
        data = self._snapshot([(7, "lead-a", "working", "\nTeam: 12", "2026-10-30T11:00:00Z")])
        data.update(observed_at="x", comments_since="2026-10-16T12:00:00+00:00")
        output = markdown(data)
        self.assertIn("Active headcount: 12 across 1 posting agents", output)
        self.assertIn("Comments loaded since 2026-10-16", output)


if __name__ == "__main__":
    unittest.main()
