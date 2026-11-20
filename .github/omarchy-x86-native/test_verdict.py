#!/usr/bin/env python3
"""verdict.py: passed only for complete, matching, attested evidence from this run attempt."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("verdict", Path(__file__).resolve().parent / "verdict.py")
verdict = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verdict)
SHA = "a" * 40
ARCHES = ("x86_64", "aarch64")


def expected(source="ci-run"):
    identities = {}
    for index, arch in enumerate(ARCHES):
        identities[arch] = ({"id": 100 + index, "name": f"agterm-omarchy-{arch}-{SHA}", "transport_digest": f"sha256:{index}"}
                            if source == "ci-run" else
                            {"id": 200 + index, "name": f"agterm-omarchy-{arch}.tar.gz", "sha256": f"{index}" * 64})
    return {"source": source, "sha": SHA, "label": "run7-1", "channel": "stable", "arches": list(ARCHES),
            "identities": identities}


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fresh()

    def fresh(self):
        self.evidence = Path(tempfile.mkdtemp(dir=self.temp.name))

    def leg(self, arch, plan, attempt=1, gates=None, host=None, raw=None):
        directory = self.evidence / f"omarchy-native-gates-{arch}-stable-run7-1-55-{attempt}"
        directory.mkdir()
        identity = plan["identities"][arch]
        sha256 = identity.get("sha256", "f" * 64)
        table = {"archive": sha256, "payload_commit": SHA, "host_arch": arch,
                 "ordinary": "PASS 7/7 exit=0 cleanup=True", "live": "PASS 9/9 exit=0 cleanup=True",
                 "probe": "PASS 10/10", "uninstall": "PASS exit=0 files_left=0 stray_processes=0",
                 "installed_vs_stage": "PASS 0 differences"} | (gates or {})
        (directory / "gates.tsv").write_text(raw if raw is not None else
                                             "".join(f"{key}\t{value}\n" for key, value in table.items()))
        record = {"arch": arch, "source": plan["source"], "identity": identity, "archive": "archive.tar.gz",
                  "sha256": sha256, "commit": SHA, "attestation": "verified"} | (host or {})
        (directory / "host-record.json").write_text(json.dumps(record))

    def decide(self, plan, attempt=1, source="success", gates="success"):
        return verdict.decide(self.evidence, plan, "55", str(attempt), source, gates)

    def test_complete_attested_evidence_passes_and_reports_the_tested_bytes(self):
        for source in ("ci-run", "release"):
            with self.subTest(source):
                self.fresh()
                plan = expected(source)
                for arch in ARCHES:
                    self.leg(arch, plan)
                result = self.decide(plan)
                self.assertEqual(result["errors"], [])
                self.assertTrue(result["passed"])
                self.assertEqual(set(result["archives"]), set(ARCHES))
                for arch in ARCHES:
                    self.assertEqual(result["archives"][arch]["id"], plan["identities"][arch]["id"])
                    self.assertEqual(result["archives"][arch]["attestation"], "verified")

    def test_missing_evidence_fails_even_when_every_job_succeeded(self):
        plan = expected()
        self.leg("x86_64", plan)
        result = self.decide(plan)
        self.assertFalse(result["passed"])
        self.assertIn("aarch64: no evidence from attempt 1", result["errors"][0])

    def test_an_empty_duplicate_or_unknown_architecture_list_never_passes(self):
        plan = expected()
        for arch in ARCHES:
            self.leg(arch, plan)
        for arches in ([], ["x86_64", "x86_64"], ["x86_64", "riscv64"], None, "x86_64"):
            with self.subTest(arches=arches):
                result = self.decide(dict(plan, arches=arches))
                self.assertFalse(result["passed"])
                self.assertIn(f"invalid architecture list {arches!r}", result["errors"])

    def test_a_partial_rerun_reuses_no_earlier_attempt(self):
        plan = expected()
        self.leg("x86_64", plan, attempt=1)
        self.leg("aarch64", plan, attempt=2)
        result = self.decide(plan, attempt=2)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("x86_64: no evidence from attempt 2", result["errors"][0])

    def test_mixed_outcomes_fail(self):
        cases = {"ordinary": {"ordinary": "FAIL 0/7 exit=1"}, "live": {"live": "PASS 8/9 exit=0"},
                 "probe": {"probe": "PASS 10/100"}, "uninstall": {"uninstall": "FAIL exit=0"}}
        for gate, change in cases.items():
            with self.subTest(gate):
                self.fresh()
                plan = expected()
                self.leg("x86_64", plan)
                self.leg("aarch64", plan, gates=change)
                result = self.decide(plan)
                self.assertFalse(result["passed"])
                self.assertEqual(result["errors"], [f"aarch64: {gate} gate does not match"])
        plan = expected()
        self.fresh()
        for arch in ARCHES:
            self.leg(arch, plan)
        self.assertIn("gates jobs failure", self.decide(plan, gates="failure")["errors"])
        self.assertIn("source job cancelled", self.decide(plan, source="cancelled")["errors"])

    def test_evidence_that_does_not_match_the_resolved_bytes_fails(self):
        plan = expected("release")
        cases = {
            "resolved artifact identity": {"host": {"identity": {"id": 999}}},
            "host commit": {"host": {"commit": "b" * 40}},
            "payload commit": {"gates": {"payload_commit": "b" * 40}},
            "archive sha256": {"gates": {"archive": "e" * 64}},
            "release asset sha256": {"host": {"sha256": "e" * 64}, "gates": {"archive": "e" * 64}},
            "tested architecture": {"gates": {"host_arch": "x86_64"}},
            "host record architecture": {"host": {"arch": "x86_64"}},
            "installed bytes": {"gates": {"installed_vs_stage": "FAIL 1 differences"}},
        }
        for check, change in cases.items():
            with self.subTest(check):
                self.fresh()
                self.leg("x86_64", plan)
                self.leg("aarch64", plan, **change)
                result = self.decide(plan)
                self.assertFalse(result["passed"])
                self.assertIn(f"aarch64: {check} does not match", result["errors"])

    def test_malformed_or_repeated_rows_fail(self):
        for raw in ("ordinary\tPASS 7/7 x\nordinary\tFAIL 0/7\n", "no tab here\n"):
            with self.subTest(raw):
                self.fresh()
                plan = expected()
                self.leg("x86_64", plan)
                self.leg("aarch64", plan, raw=raw)
                result = self.decide(plan)
                self.assertFalse(result["passed"])
                self.assertIn("aarch64: unreadable evidence", result["errors"][0])

    def test_an_unattested_archive_never_passes_and_fails_the_job_unless_report_only(self):
        plan = expected()
        self.leg("x86_64", plan)
        self.leg("aarch64", plan, host={"attestation": "unverified"})
        result = self.decide(plan)
        self.assertEqual(result["errors"], [])
        self.assertFalse(result["passed"])
        self.assertFalse(result["attested"])
        plan_file = self.evidence / "expected.json"
        plan_file.write_text(json.dumps(plan))
        for policy, code in (("required", 1), ("report-only", 0)):
            with self.subTest(policy), tempfile.NamedTemporaryFile("r") as output:
                argv = ["verdict.py", "--evidence", str(self.evidence), "--expected", str(plan_file), "--run-id", "55",
                        "--attempt", "1", "--source-result", "success", "--gates-result", "success",
                        "--attestation", policy]
                with patch("sys.argv", argv), patch.dict(os.environ, {"GITHUB_OUTPUT": output.name}), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_:
                    verdict.main()
                self.assertEqual(exit_.exception.code, code)
                self.assertIn("passed=false\n", Path(output.name).read_text())

    def test_report_only_never_passes_even_with_complete_attested_evidence(self):
        plan = expected()
        for arch in ARCHES:
            self.leg(arch, plan)
        self.assertTrue(self.decide(plan)["passed"])
        plan_file = self.evidence / "expected.json"
        plan_file.write_text(json.dumps(plan))
        for policy, passed in (("required", "true"), ("report-only", "false")):
            with self.subTest(policy), tempfile.NamedTemporaryFile("r") as output:
                argv = ["verdict.py", "--evidence", str(self.evidence), "--expected", str(plan_file), "--run-id", "55",
                        "--attempt", "1", "--source-result", "success", "--gates-result", "success",
                        "--attestation", policy]
                with patch("sys.argv", argv), patch.dict(os.environ, {"GITHUB_OUTPUT": output.name}), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_:
                    verdict.main()
                self.assertEqual(exit_.exception.code, 0)
                self.assertIn(f"passed={passed}\n", Path(output.name).read_text())


if __name__ == "__main__":
    unittest.main()
