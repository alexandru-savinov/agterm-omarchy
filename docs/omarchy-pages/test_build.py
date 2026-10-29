#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("pages_builder", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class PagesTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
