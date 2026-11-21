import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pr_fields", ROOT / "scripts/github/pr_fields.py")
pr_fields = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr_fields)

HEAD = "a" * 40
OTHER = "b" * 40
FIELD_LINES = (f"Task: #380\nAgent: sancta_ghmanager\nReviewed head: {HEAD}\n"
               "Review: https://github.com/o/r/pull/1#issuecomment-2\n")
GOOD = FIELD_LINES + "\n## Problem\nText\n"


class PrFieldsTests(unittest.TestCase):
    def test_complete_body_for_the_current_head_passes(self):
        self.assertEqual(pr_fields.problems(GOOD, HEAD), [])
        self.assertEqual(pr_fields.problems(GOOD.replace("\n", "\r\n"), HEAD), [])
        self.assertEqual(pr_fields.problems(GOOD.replace("\n", "\r"), HEAD), [])

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
        for body in ("", None, "\n\n  \n"):
            self.assertEqual(len(pr_fields.problems(body, HEAD)), 4)

    def test_invalid_values_fail(self):
        for name, bad in (("Task", "380"), ("Task", "#"), ("Agent", "has space"), ("Agent", ""),
                          ("Reviewed head", "abc1234"), ("Reviewed head", HEAD.upper()),
                          ("Review", "http://github.com/o/r/pull/1"), ("Review", "see above")):
            lines = [f"{name}: {bad}" if line.startswith(name + ":") else line for line in GOOD.split("\n")]
            self.assertTrue(pr_fields.problems("\n".join(lines), HEAD), (name, bad))


class LeadingBlockTests(unittest.TestCase):
    """The fields must be the first non-blank lines of the body; nothing after the block is read."""

    def test_blank_lines_before_the_block_and_any_field_order_are_fine(self):
        reordered = "\n\n" + "\n".join(reversed(FIELD_LINES.strip().split("\n"))) + "\n\nText\n"
        self.assertEqual(pr_fields.problems(reordered, HEAD), [])

    def test_fields_after_any_other_line_read_as_missing(self):
        for prefix in ("Intro\n", "## Heading\n", "```\n", "<!--\n", "> quote\n", "  indented\n", "<div>\n"):
            self.assertEqual(len(pr_fields.problems(prefix + FIELD_LINES, HEAD)), 4, prefix)

    def test_an_interrupted_block_loses_only_the_fields_after_the_break(self):
        lines = FIELD_LINES.split("\n")
        body = "\n".join(lines[:2] + ["Note"] + lines[2:])
        found = pr_fields.problems(body, HEAD)
        self.assertEqual(len(found), 2)
        self.assertTrue(all("missing field" in item for item in found), found)

    def test_a_blank_line_ends_the_block(self):
        lines = FIELD_LINES.split("\n")
        body = "\n".join(lines[:3] + [""] + lines[3:])
        self.assertEqual(len(pr_fields.problems(body, HEAD)), 1)

    def test_a_duplicate_inside_the_block_fails(self):
        found = pr_fields.problems("Task: #1\n" + GOOD, HEAD)
        self.assertTrue(any("appears 2 times" in item for item in found), found)

    def test_repeated_fields_after_the_block_are_ignored_not_trusted(self):
        self.assertEqual(pr_fields.problems(GOOD + "Task: #999\nReviewed head: " + OTHER + "\n", HEAD), [])
        self.assertTrue(pr_fields.problems(GOOD.replace(HEAD, OTHER) + "Reviewed head: " + HEAD + "\n", HEAD))

    def test_a_forged_review_head_in_prose_cannot_satisfy_the_check(self):
        body = f"I reviewed {HEAD} myself.\nTask: #1\nAgent: x\nReview: https://github.com/o/r/pull/1\n"
        self.assertTrue(any("Reviewed head" in item for item in pr_fields.problems(body, HEAD)))



def fold(fields=FIELD_LINES, opener=pr_fields.FOLD_OPEN, close=pr_fields.FOLD_CLOSE):
    return f"{opener}\n\n{fields}\n{close}\n"


class ReviewRecordFoldTests(unittest.TestCase):
    """The fields may instead sit in one closed `Review record` fold, anywhere after the human sections."""

    BODY = "## What changes for users\nText\n\n"

    def test_a_filled_fold_at_the_bottom_passes(self):
        self.assertEqual(pr_fields.problems(self.BODY + fold(), HEAD), [])
        self.assertEqual(pr_fields.problems((self.BODY + fold()).replace("\n", "\r\n"), HEAD), [])
        self.assertEqual(pr_fields.problems(self.BODY + fold() + "\nGenerated footer\n", HEAD), [])

    def test_a_stale_head_in_the_fold_fails(self):
        found = pr_fields.problems(self.BODY + fold(FIELD_LINES.replace(HEAD, OTHER)), HEAD)
        self.assertEqual(len(found), 1)
        self.assertIn("stale", found[0])

    def test_an_unclosed_misnamed_or_indented_fold_reads_as_missing(self):
        for body in (self.BODY + fold(close=""), self.BODY + fold(close="</detail>"),
                     self.BODY + fold(opener="<details><summary>Agent record</summary>"),
                     self.BODY + fold(opener="  " + pr_fields.FOLD_OPEN),
                     self.BODY + fold(fields="Note\n" + FIELD_LINES)):
            self.assertEqual(len(pr_fields.problems(body, HEAD)), 4, body)

    def test_two_folds_fail(self):
        found = pr_fields.problems(self.BODY + fold() + fold(), HEAD)
        self.assertTrue(any("appears 2 times; keep exactly one" in item and "Review record" in item
                            for item in found), found)

    def test_a_field_in_both_places_is_repeated(self):
        found = pr_fields.problems(FIELD_LINES + "\n" + self.BODY + fold(), HEAD)
        self.assertEqual(len(found), 5)
        self.assertTrue(any("in one place" in item for item in found), found)
        self.assertEqual(sum("appears 2 times" in item for item in found), 4, found)

    def test_a_record_split_between_the_two_places_fails(self):
        lines = FIELD_LINES.strip().split("\n")
        body = "\n".join(lines[:2]) + "\n\n" + self.BODY + fold("\n".join(lines[2:]) + "\n")
        found = pr_fields.problems(body, HEAD)
        self.assertEqual(len(found), 1)
        self.assertIn("in one place", found[0])

    def test_a_bare_template_line_ends_the_fold_block(self):
        body = self.BODY + fold(FIELD_LINES.replace("Agent: sancta_ghmanager", "Agent:"))
        found = pr_fields.problems(body, HEAD)
        self.assertEqual(len(found), 4)
        self.assertTrue(all("missing field" in item for item in found), found)

    def test_the_leading_block_still_passes_with_no_fold(self):
        self.assertEqual(pr_fields.problems(GOOD, HEAD), [])

    def test_the_real_template_fails_until_filled_in_and_passes_once_it_is(self):
        template = (ROOT / ".github/pull_request_template.md").read_text()
        self.assertTrue(pr_fields.problems(template, HEAD))
        self.assertNotIn("<!--", template)
        self.assertTrue(template.startswith("## What changes for users\n"), template[:80])
        lines = template.split("\n")
        opener = lines.index(pr_fields.FOLD_OPEN)
        self.assertGreater(opener, max(index for index, line in enumerate(lines) if line.startswith("## ")))
        empty = "Task: #\nAgent:\nReviewed head:\nReview:\n"
        self.assertEqual(template.count(empty), 1)
        self.assertEqual(pr_fields.problems(template.replace(empty, FIELD_LINES), HEAD), [])


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


class MainTests(unittest.TestCase):
    """main() reads the event payload and turns problems into an exit code and annotations."""

    def run_main(self, body, draft=False, bot=False, created="2026-11-20T00:00:00Z", cutoff=None):
        pull = {"user": {"type": "Bot" if bot else "User"}, "draft": draft, "body": body,
                "head": {"sha": HEAD}, "created_at": created}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            path.write_text(json.dumps({"pull_request": pull}))
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(path)}), \
                    mock.patch.object(pr_fields, "ENFORCED_FROM", cutoff or pr_fields.ENFORCED_FROM), \
                    contextlib.redirect_stdout(out):
                code = pr_fields.main()
        return code, out.getvalue()

    def test_a_good_body_passes_whatever_the_cutoff(self):
        code, out = self.run_main(GOOD, cutoff="2026-11-16T00:00:00Z")
        self.assertEqual(code, 0)
        self.assertIn("PR fields OK", out)

    def test_a_bad_body_after_the_cutoff_is_an_error_and_exit_1(self):
        code, out = self.run_main("nothing here", cutoff="2026-11-16T00:00:00Z")
        self.assertEqual(code, 1)
        self.assertIn("::error", out)
        self.assertNotIn("::warning", out)

    def test_a_bad_body_before_the_cutoff_is_a_warning_and_exit_0(self):
        code, out = self.run_main("nothing here", created="2026-11-10T00:00:00Z", cutoff="2026-11-16T00:00:00Z")
        self.assertEqual(code, 0)
        self.assertIn("::warning", out)
        self.assertNotIn("::error", out)

    def test_with_the_default_cutoff_nothing_ever_fails(self):
        code, out = self.run_main("nothing here")
        self.assertEqual(code, 0)
        self.assertIn("::warning", out)

    def test_drafts_and_bots_are_skipped_even_with_a_bad_body_after_the_cutoff(self):
        for kwargs in ({"draft": True}, {"bot": True}):
            code, out = self.run_main("nothing here", cutoff="2026-11-16T00:00:00Z", **kwargs)
            self.assertEqual(code, 0, kwargs)
            self.assertNotIn("::error", out)
            self.assertNotIn("::warning", out)

    def test_a_missing_body_is_handled(self):
        code, out = self.run_main(None, cutoff="2026-11-16T00:00:00Z")
        self.assertEqual(code, 1)
        self.assertEqual(out.count("::error"), 4)


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

    def test_the_workflow_triggers_cover_drafts_and_edits(self):
        text = (ROOT / ".github/workflows/pr-fields.yml").read_text()
        match = re.search(r"^    types: \[(.*)\]\s*$", text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual({item.strip() for item in match.group(1).split(",")},
                         {"opened", "edited", "synchronize", "reopened", "ready_for_review", "converted_to_draft"})


if __name__ == "__main__":
    unittest.main()
