import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pr_fields", ROOT / "scripts/github/pr_fields.py")
pr_fields = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr_fields)

HEAD = "a" * 40
OTHER = "b" * 40
GOOD = (f"Task: #380\nAgent: sancta_ghmanager\nReviewed head: {HEAD}\n"
        "Review: https://github.com/o/r/pull/1#issuecomment-2\n\n## Problem\nText\n")


class PrFieldsTests(unittest.TestCase):
    def test_complete_body_for_the_current_head_passes(self):
        self.assertEqual(pr_fields.problems(GOOD, HEAD), [])
        self.assertEqual(pr_fields.problems(GOOD.replace("\n", "\r\n"), HEAD), [])

    def test_stale_review_fails(self):
        found = pr_fields.problems(GOOD, OTHER)
        self.assertEqual(len(found), 1)
        self.assertIn("stale", found[0])

    def test_every_missing_field_is_named(self):
        for name in pr_fields.FIELDS:
            body = "\n".join(line for line in GOOD.split("\n") if not line.startswith(name + ":"))
            found = pr_fields.problems(body, HEAD)
            self.assertTrue(any("missing field" in item and name in item for item in found), (name, found))

    def test_empty_and_none_bodies_fail_all_four(self):
        for body in ("", None):
            self.assertEqual(len(pr_fields.problems(body, HEAD)), 4)

    def test_invalid_values_fail(self):
        for name, bad in (("Task", "380"), ("Task", "#"), ("Agent", "has space"), ("Agent", ""),
                          ("Reviewed head", "abc1234"), ("Reviewed head", HEAD.upper()),
                          ("Review", "http://github.com/o/r/pull/1"), ("Review", "see above")):
            lines = [f"{name}: {bad}" if line.startswith(name + ":") else line for line in GOOD.split("\n")]
            self.assertTrue(pr_fields.problems("\n".join(lines), HEAD), (name, bad))

    def test_duplicates_fail_even_when_both_are_valid(self):
        found = pr_fields.problems(GOOD + "Task: #381\n", HEAD)
        self.assertTrue(any("appears 2 times" in item for item in found), found)

    def test_indented_fields_do_not_count(self):
        indented = "\n".join("  " + line for line in GOOD.split("\n"))
        self.assertEqual(len(pr_fields.problems(indented, HEAD)), 4)

    def test_a_forged_review_head_in_prose_cannot_satisfy_the_check(self):
        body = f"I reviewed {HEAD} myself.\nTask: #1\nAgent: x\nReview: https://github.com/o/r/pull/1\n"
        self.assertTrue(any("Reviewed head" in item for item in pr_fields.problems(body, HEAD)))


class HiddenTextTests(unittest.TestCase):
    """Fields in code fences and HTML comments are not visible Markdown text and never count."""

    def test_plain_fence_hides_fields(self):
        self.assertEqual(len(pr_fields.problems("```\n" + GOOD + "```\n", HEAD)), 4)
        self.assertEqual(len(pr_fields.problems("~~~\n" + GOOD + "~~~\n", HEAD)), 4)

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        body = "````\n```\n" + GOOD + "````\n"
        self.assertEqual(len(pr_fields.problems(body, HEAD)), 4)
        shorter_closes_wrongly = "````\n```\nTask: #1\n````\n" + GOOD
        self.assertEqual(pr_fields.problems(shorter_closes_wrongly, HEAD), [])

    def test_fence_markers_must_match_and_may_be_indented_up_to_three_spaces(self):
        self.assertEqual(len(pr_fields.problems("   ```\n" + GOOD + "   ```\n", HEAD)), 4)
        # A tilde fence is not closed by backticks, so the fields stay hidden.
        self.assertEqual(len(pr_fields.problems("~~~\n```\n" + GOOD, HEAD)), 4)
        # Four spaces is indented code, not a fence: the fields below it are visible.
        self.assertEqual(pr_fields.problems("    ```\n" + GOOD, HEAD), [])

    def test_unclosed_fence_and_unclosed_comment_hide_the_rest_so_they_fail_never_pass(self):
        self.assertEqual(len(pr_fields.problems("```\n" + GOOD, HEAD)), 4)
        self.assertEqual(len(pr_fields.problems("<!--\n" + GOOD, HEAD)), 4)

    def test_html_comments_hide_fields_and_fields_after_the_comment_count(self):
        self.assertEqual(len(pr_fields.problems("<!--\n" + GOOD + "-->\n", HEAD)), 4)
        self.assertEqual(pr_fields.problems("<!-- note -->\n" + GOOD, HEAD), [])

    def test_the_real_template_comment_does_not_count_as_a_duplicate(self):
        template = (ROOT / ".github/pull_request_template.md").read_text()
        comment = template[template.index("<!--"):template.index("-->") + 3]
        self.assertIn("Reviewed head:", comment)
        self.assertEqual(pr_fields.problems(comment + "\n" + GOOD, HEAD), [])


class GrandfatherTests(unittest.TestCase):
    def test_pull_requests_opened_before_the_cutoff_warn_and_later_ones_fail(self):
        since = "2026-11-16T00:00:00Z"
        self.assertFalse(pr_fields.enforced("2026-11-15T23:59:59Z", since))
        self.assertTrue(pr_fields.enforced("2026-11-16T00:00:00Z", since))

    def test_a_missing_creation_date_warns_and_never_fails(self):
        for missing in (None, ""):
            self.assertFalse(pr_fields.enforced(missing, "2026-11-16T00:00:00Z"))

    def test_default_cutoff_enforces_nothing_until_it_is_set(self):
        self.assertFalse(pr_fields.enforced("2026-11-15T00:00:00Z"))


class WorkflowGuardTests(unittest.TestCase):
    """The step must pass when the base commit has no checker (the PR that adds it), and run it when it does."""

    @staticmethod
    def step_script():
        text = (ROOT / ".github/workflows/pr-fields.yml").read_text()
        block = text.split("run: |\n", 1)[1]
        return "\n".join(line[10:] for line in block.split("\n") if line.startswith("          "))

    def run_step(self, with_checker):
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "bin").mkdir()
            (root / "bin/python3").write_text("#!/bin/sh\necho ran-checker \"$@\"\nexit 7\n")
            (root / "bin/python3").chmod(0o755)
            if with_checker:
                (root / "scripts/github").mkdir(parents=True)
                (root / "scripts/github/pr_fields.py").write_text("")
            env = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"])
            return subprocess.run([bash, "-e", "-c", self.step_script()], cwd=root, capture_output=True,
                                  text=True, env=env)

    def test_missing_checker_is_a_notice_and_success(self):
        result = self.run_step(with_checker=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice", result.stdout)
        self.assertNotIn("ran-checker", result.stdout)

    def test_present_checker_runs_and_its_failure_is_not_swallowed(self):
        result = self.run_step(with_checker=True)
        self.assertEqual(result.returncode, 7)
        self.assertIn("ran-checker scripts/github/pr_fields.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
