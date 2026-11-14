import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("pr_fields", Path(__file__).resolve().parents[1] / "github/pr_fields.py")
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

    def test_fields_in_code_fences_or_indented_do_not_count(self):
        fenced = "```\n" + GOOD + "```\n"
        self.assertEqual(len(pr_fields.problems(fenced, HEAD)), 4)
        indented = "\n".join("  " + line for line in GOOD.split("\n"))
        self.assertEqual(len(pr_fields.problems(indented, HEAD)), 4)

    def test_a_forged_review_head_in_prose_cannot_satisfy_the_check(self):
        body = f"I reviewed {HEAD} myself.\nTask: #1\nAgent: x\nReview: https://github.com/o/r/pull/1\n"
        self.assertTrue(any("Reviewed head" in item for item in pr_fields.problems(body, HEAD)))


if __name__ == "__main__":
    unittest.main()
