import unittest
from queue import checkin, markdown, snapshot


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
        self.assertEqual(result, {"tasks": [], "agents": [], "claims": [], "uncertain_observations": []})

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
            self.assertIsNone(data["claims"][0]["latest"][key])
            self.assertTrue(any("duplicate" in note for note in data["claims"][0]["latest"]["uncertainties"]))
        data = snapshot([self.issue(1)], {1: [self.claim(task=99, state="available")]})
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["observed_claims"], [])
        self.assertIsNone(data["claims"][0]["latest"]["state"])
        self.assertIn("task: issue absent from this task snapshot", data["claims"][0]["latest"]["uncertainties"])

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
                    "Agent: agent\nTask: #1\nOwned paths: `\nState: complete\n`"]
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


if __name__ == "__main__":
    unittest.main()
