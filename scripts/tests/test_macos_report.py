import copy
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("macos_report", Path(__file__).resolve().parents[1] / "github/macos_report.py")
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


class AdvisoryReportTests(unittest.TestCase):
    def setUp(self):
        self.run = {"id": 2, "run_attempt": 1, "run_number": 3, "workflow_id": 4, "event": "pull_request",
                    "path": reporter.WORKFLOW, "repository": {"full_name": reporter.REPO}, "status": "completed",
                    "head_repository": {"id": 8, "owner": {"login": "contributor"}}, "head_branch": "core/fix",
                    "head_sha": "a" * 40, "pull_requests": []}
        self.pr = {"number": 9, "state": "open", "base": {"repo": {"full_name": reporter.REPO}}, "labels": [],
                   "head": {"sha": self.run["head_sha"], "ref": "core/fix", "repo": {"id": 8}}}
        self.job = {"name": reporter.JOB, "status": "completed", "conclusion": "failure",
                    "steps": [{"name": "make test", "conclusion": "failure"}]}
        self.latest = self.run
        self.runs = [self.run]
        self.current = self.pr
        self.writes = []

    def api(self, path, method="GET", data=None, pages=False):
        if method != "GET":
            self.writes.append((method, path, data))
        elif path == "actions/runs/2":
            return self.latest
        elif "/attempts/" in path:
            self.assertTrue(pages)
            return [{"jobs": [self.job]}]
        elif path.startswith("pulls?"):
            self.assertIn("head=contributor%3Acore%2Ffix", path)
            return [[self.pr]]
        elif path == "pulls/9":
            return self.current
        elif path.startswith("actions/workflows/4/runs?"):
            return [{"workflow_runs": self.runs}]
        elif path == "labels?per_page=100":
            return [[{"name": reporter.LABEL}]]
        else:
            self.fail(f"unexpected request: {path}")

    def test_failure_on_current_fork_pr_labels_and_comments(self):
        reporter.report(self.run, self.api)
        self.assertEqual(self.writes[:1], [("POST", "issues/9/labels", {"labels": ["macos-broken"]})])
        self.assertEqual(self.writes[1][:2], ("POST", "issues/9/comments"))
        self.assertIn("failed. [Run details]", self.writes[1][2]["body"])
        self.assertIn("/actions/runs/2/attempts/1", self.writes[1][2]["body"])

    def test_timeout_is_reported_and_later_success_removes_label(self):
        self.pr["labels"] = [{"name": reporter.LABEL}]
        self.job["conclusion"] = "timed_out"
        reporter.report(self.run, self.api)
        self.assertEqual(len(self.writes), 1)
        self.assertIn("timed out.", self.writes[0][2]["body"])
        self.writes.clear()
        self.job["conclusion"] = "success"
        reporter.report(self.run, self.api)
        self.assertEqual(self.writes, [("DELETE", "issues/9/labels/macos-broken", None)])

    def test_renamed_or_skipped_job_cannot_update_labels(self):
        self.job["name"] = "Detect changes"
        reporter.report(self.run, self.api)
        self.job.update(name=reporter.JOB, conclusion="skipped")
        reporter.report(self.run, self.api)
        self.assertEqual(self.writes, [])

    def test_superseded_attempt_and_later_run_are_ignored(self):
        self.latest = {**self.run, "run_attempt": 2}
        reporter.report(self.run, self.api)
        self.latest = self.run
        self.runs.append({**self.run, "id": 5, "run_number": 4})
        reporter.report(self.run, self.api)
        self.assertEqual(self.writes, [])

    def test_new_head_and_other_fork_are_ignored(self):
        self.current = copy.deepcopy(self.pr)
        self.current["head"]["sha"] = "b" * 40
        reporter.report(self.run, self.api)
        self.current["head"]["sha"] = self.run["head_sha"]
        self.current["head"]["repo"]["id"] = 10
        reporter.report(self.run, self.api)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
