#!/usr/bin/env python3
"""Check that the automatic release graph can handle failed and skipped ancestors."""

from pathlib import Path
import re
import unittest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
JOB = re.compile(r"^  ([a-zA-Z0-9_-]+):\s*$", re.MULTILINE)
FIELD = re.compile(r"^    ([a-zA-Z0-9_-]+):([^\n]*)", re.MULTILINE)
STATUS_CHECK = re.compile(r"(?:!\s*cancelled|\balways)\(\)")
RESULT_CHECK = re.compile(r"\bneeds\.([a-zA-Z0-9_-]+)\.result\b")


def read_jobs(path):
    """Read job-level keys and their continuations without a YAML dependency."""
    lines = path.read_text().split("\njobs:\n", 1)[1]
    jobs = {}
    matches = list(JOB.finditer(lines))
    for index, job in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(lines)
        block = lines[job.end():end]
        fields = {}
        keys = list(FIELD.finditer(block))
        for number, field in enumerate(keys):
            end = keys[number + 1].start() if number + 1 < len(keys) else len(block)
            tail = block[field.end():end].strip()
            fields[field[1]] = "\n".join((field[2].strip(), tail)).strip()
        jobs[job[1]] = fields
    return jobs


def dependencies(value):
    if value.startswith("- "):
        return {line.removeprefix("- ").strip() for line in map(str.strip, value.splitlines()) if line}
    return {item.strip() for item in value.strip("[]").split(",") if item.strip()}


class WorkflowTests(unittest.TestCase):
    def test_dependent_jobs_override_implicit_success_and_check_a_dependency(self):
        for name in ("auto-release.yml", "auto-release-watch.yml"):
            jobs = read_jobs(WORKFLOWS / name)
            self.assertTrue(jobs)
            for job, fields in jobs.items():
                if "needs" not in fields:
                    continue
                with self.subTest(workflow=name, job=job):
                    direct = dependencies(fields["needs"])
                    self.assertTrue(direct and direct <= jobs.keys(), "expected valid direct dependencies")
                    condition = fields.get("if", "")
                    self.assertRegex(condition, STATUS_CHECK, "job relies on implicit success()")
                    self.assertTrue(set(RESULT_CHECK.findall(condition)) & direct,
                                    "condition must check a direct dependency result")

    def test_dry_run_routes_a_synthetic_verdict_through_the_shared_retract_job(self):
        release = read_jobs(WORKFLOWS / "auto-release.yml")
        watch = read_jobs(WORKFLOWS / "auto-release-watch.yml")
        self.assertEqual(release["watch_dry_run"]["uses"], release["watch_promotion"]["uses"])
        self.assertIn("dry_run: true", release["watch_dry_run"]["with"])
        self.assertEqual(dependencies(watch["retract"]["needs"]), {"evaluate"})
        self.assertIn("needs.evaluate.outputs.action == 'retract'", watch["retract"]["if"])
        self.assertIn("synthetic_verdict", dependencies(watch["evaluate"]["needs"]))
        self.assertIn("inputs.dry_run && needs.synthetic_verdict.result == 'success'", watch["evaluate"]["if"])
        self.assertIn("needs.synthetic_verdict.outputs.attempts", watch["evaluate"]["steps"])
        self.assertIn("inputs.dry_run &&", watch["synthetic_verdict"]["if"])
        self.assertIn("!inputs.dry_run", watch["native_published"]["if"])
        self.assertIn("--rehearse-retract", watch["retract"]["steps"])
        self.assertIn("!inputs.dry_run && github.event_name !=", watch["retract"]["steps"])


if __name__ == "__main__":
    unittest.main()
