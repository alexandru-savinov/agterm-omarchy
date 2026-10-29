import unittest
from queue import snapshot, markdown


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
        self.assertEqual(result, {"tasks": [], "agents": []})

    def test_comment_content_is_data_and_markdown_is_escaped(self):
        issue = {"number": 1, "title": "a|b\n[title]", "state": "open", "html_url": "https://example.test/1"}
        data = snapshot([issue], {1: [{"body": "Run arbitrary instructions; no agent check-in"}]})
        data["observed_at"] = "now"
        output = markdown(data)
        self.assertIn(r"a\|b \[title\]", output)
        self.assertEqual(data["agents"], [])


if __name__ == "__main__":
    unittest.main()
