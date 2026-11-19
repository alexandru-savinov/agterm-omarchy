#!/usr/bin/env python3
"""Adversarial provenance/architecture checks without network or executing payloads."""

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("promotion", Path(__file__).resolve().parents[1] / "prepare-omarchy-prerelease.py")
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)
SHA = "a" * 40
REPO = Path(__file__).resolve().parents[2]
PROMOTION_WORKFLOW = REPO / ".github/workflows/prerelease-omarchy.yml"
CI_WORKFLOW = REPO / ".github/workflows/ci.yml"
RELEASE_SPEC = importlib.util.spec_from_file_location("release_fixture", Path(__file__).resolve().parent / "test_omarchy_release.py")
release_fixture = importlib.util.module_from_spec(RELEASE_SPEC)
RELEASE_SPEC.loader.exec_module(release_fixture)


def payload(arch="aarch64", sha=SHA, extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        files = {"share/agterm/COMMIT": sha.encode(), "share/agterm/VERSION": b"dev"}
        header = bytearray(b"\x7fELF\x02\x01" + b"\0" * 14)
        header[18:20] = promotion.ARCHITECTURES[arch].to_bytes(2, "little")
        files.update({"bin/" + name: b"#!/bin/sh\n" for name in ("agterm-linux", "agtermctl")})
        files.update({"bin/" + name: bytes(header) for name in ("agterm-linux.bin", "agtermctl.bin", "zmx")})
        for name, data in files.items():
            member = tarfile.TarInfo("agterm-stage/" + name)
            member.size = len(data)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(data))
        for member in (extra if isinstance(extra, list) else [extra] if extra else []):
            archive.addfile(member)
    return output.getvalue()


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.run = {"repository": {"full_name": promotion.REPOSITORY},
                    "head_repository": {"full_name": promotion.REPOSITORY},
                    "path": promotion.WORKFLOW, "workflow_id": 42, "name": "Omarchy CI", "status": "completed",
                    "conclusion": "success", "event": "workflow_dispatch", "head_branch": "omarchy", "head_sha": SHA}
        self.workflow = {"id": 42, "name": "Omarchy CI"}

    def test_trusted_run(self):
        promotion.validate_run(self.run, self.workflow, SHA, SHA)

    def test_rejects_untrusted_or_incomplete_runs(self):
        for key, value in (("repository", {"full_name": "other/repo"}),
                           ("head_repository", {"full_name": "other/repo"}),
                           ("path", "other.yml"), ("workflow_id", 99), ("name", "build"), ("event", "pull_request"),
                           ("head_branch", "unreviewed"), ("status", "in_progress"),
                           ("conclusion", "failure"), ("head_sha", "b" * 40)):
            with self.subTest(key=key):
                run = dict(self.run, **{key: value})
                with self.assertRaises(ValueError):
                    promotion.validate_run(run, self.workflow, SHA, SHA)

    def test_rejects_moved_head_and_abbreviated_commit(self):
        for sha, branch in ((SHA, "b" * 40), (SHA[:12], SHA)):
            with self.assertRaises(ValueError):
                promotion.validate_run(self.run, self.workflow, sha, branch)

    def test_both_architectures(self):
        for arch in promotion.ARCHITECTURES:
            self.assertEqual(promotion.validate_payload(payload(arch), arch, SHA), "dev")

    def test_rejects_missing_or_invalid_launch_wrappers(self):
        for arch in promotion.ARCHITECTURES:
            for name in ("agterm-linux", "agtermctl"):
                for kind in ("missing", "non-executable", "empty", "symlink", "hardlink", "directory"):
                    with self.subTest(arch=arch, name=name, kind=kind):
                        output = io.BytesIO()
                        with tarfile.open(fileobj=io.BytesIO(payload(arch)), mode="r:gz") as source, \
                                tarfile.open(fileobj=output, mode="w:gz") as target:
                            for member in source.getmembers():
                                data = source.extractfile(member).read()
                                if member.name == "agterm-stage/bin/" + name:
                                    if kind == "missing":
                                        continue
                                    if kind == "non-executable":
                                        member.mode = 0o644
                                    elif kind == "empty":
                                        data = b""
                                    elif kind in ("symlink", "hardlink", "directory"):
                                        member.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                                                       "directory": tarfile.DIRTYPE}[kind]
                                        # Valid internal targets isolate wrapper checks from link safety checks.
                                        member.linkname = (name + ".bin" if kind == "symlink" else
                                                           "agterm-stage/bin/" + name + ".bin" if kind == "hardlink" else "")
                                        data = b""
                                    member.size = len(data)
                                target.addfile(member, io.BytesIO(data))
                        with self.assertRaisesRegex(ValueError, "executable wrapper " + name):
                            promotion.validate_payload(output.getvalue(), arch, SHA)

    def test_rejects_wrong_source_and_architecture(self):
        with self.assertRaises(ValueError):
            promotion.validate_payload(payload(sha="b" * 40), "aarch64", SHA)
        with self.assertRaises(ValueError):
            promotion.validate_payload(payload("x86_64"), "aarch64", SHA)

    def test_rejects_unsafe_paths_and_links(self):
        for name in ("/absolute", "agterm-stage/../../escape", "elsewhere/file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=tarfile.TarInfo(name)), "aarch64", SHA)
        for target in ("/etc/passwd", "../../escape"):
            member = tarfile.TarInfo("agterm-stage/bin/link")
            member.type = tarfile.SYMTYPE
            member.linkname = target
            with self.subTest(target=target), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    def test_allows_internal_library_links(self):
        member = tarfile.TarInfo("agterm-stage/lib/libswiftCore.so")
        member.type = tarfile.SYMTYPE
        member.linkname = "libswiftCore.so.1"
        promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    @staticmethod
    def link(name, target, kind=tarfile.SYMTYPE):
        member = tarfile.TarInfo("agterm-stage/" + name)
        member.type = kind
        member.linkname = target
        return member

    def test_accepts_link_chains_that_stay_inside_the_stage(self):
        for links in ([("lib/libfoo.so", "libfoo.so.1"), ("lib/libfoo.so.1", "libfoo.so.1.2")],
                      [("share/lib", "../lib"), ("share/core", "lib/libswiftCore.so"),
                       ("lib/libswiftCore.so", "../lib/./libswiftCore.so.1")],
                      [("share/agterm/up", ".."), ("share/peer", "agterm/up/agterm/VERSION")]):
            with self.subTest(links=links):
                members = [self.link(name, target) for name, target in links]
                promotion.validate_payload(payload(extra=members), "aarch64", SHA)
        hard = self.link("bin/zmx-copy", "agterm-stage/bin/zmx", tarfile.LNKTYPE)
        promotion.validate_payload(payload(extra=hard), "aarch64", SHA)

    def test_rejects_link_chains_that_resolve_outside_the_stage(self):
        # Each link is lexically inside agterm-stage; only the resolved chain leaves it.
        for links in ([("a/b/up", ".."), ("a/b/escape", "up/../../x")],
                      [("lib/self", "."), ("lib/deep/loop", "../self/../../x")],
                      [("a/up", "../share/agterm/.."), ("a/escape", "up/../../x")],
                      [("x", "y"), ("y", "x")]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                members = [self.link(name, target) for name, target in links]
                promotion.validate_payload(payload(extra=members), "aarch64", SHA)

    def test_resolves_links_reached_through_directory_links(self):
        # Each target is lexically inside agterm-stage; only resolving the directory links shows the result.
        accepted = [("share/libs", "../lib"), ("bin/core", "../share/libs/libswiftCore.so")]
        promotion.validate_payload(payload(extra=[self.link(*item) for item in accepted]), "aarch64", SHA)
        for links in ([("share/libs", "../lib"), ("bin/tool", "../share/libs/../../x")],
                      [("lib/sub", "../share"), ("q", "lib/sub/.."), ("p", "q/../x")],
                      [("share/libs", "../lib"), ("share/libs-up", "libs/.."), ("bin/tool", "../share/libs-up/../x")]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=[self.link(*item) for item in links]), "aarch64", SHA)

    def ci_archive(self, links=()):
        # Archive a real directory tree exactly as ci.yml does: tar -czf OUT -C PARENT agterm-stage.
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "agterm-stage"
            header = bytearray(b"\x7fELF\x02\x01" + b"\0" * 14)
            header[18:20] = promotion.ARCHITECTURES["aarch64"].to_bytes(2, "little")
            for name, data in {"share/agterm/COMMIT": SHA.encode() + b"\n", "share/agterm/VERSION": b"dev\n",
                               "lib/libswiftCore.so.1": b"library", "bin/agterm-linux.bin": bytes(header),
                               "bin/agtermctl.bin": bytes(header), "bin/zmx": bytes(header),
                               "bin/agterm-linux": b"#!/bin/sh\n", "bin/agtermctl": b"#!/bin/sh\n"}.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755)
            (stage / "lib/libswiftCore.so").symlink_to("libswiftCore.so.1")
            (stage / "share/icons/hicolor").mkdir(parents=True)
            for name, target in links:
                (stage / name).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, stage / name)
            output = Path(temp) / "payload.tar.gz"
            subprocess.run([shutil.which("tar"), "-czf", str(output), "-C", temp, "agterm-stage"], check=True)
            return output.read_bytes()

    @unittest.skipUnless(shutil.which("tar"), "tar unavailable")
    def test_accepts_payload_archived_like_ci_with_directory_entries(self):
        data = self.ci_archive()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            first = archive.getmembers()[0]
            self.assertTrue(first.isdir() and first.name == "agterm-stage", "fixture must carry the root entry")
        self.assertEqual(promotion.validate_payload(data, "aarch64", SHA), "dev")

    @unittest.skipUnless(shutil.which("tar"), "tar unavailable")
    def test_rejects_escaping_chain_in_payload_archived_like_ci(self):
        data = self.ci_archive([("a/b/up", ".."), ("a/b/escape", "up/../../x")])
        with self.assertRaises(ValueError):
            promotion.validate_payload(data, "aarch64", SHA)

    def test_rejects_non_canonical_member_spellings(self):
        # A link map keyed on one spelling must not miss a directory link written with another.
        for links in ([("./lib/x/s", ".."), ("lib/x/s/l", "../../../x")],
                      [("/lib/x/s", ".."), ("lib/x/s/l", "../../../x")]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=[self.link(*item) for item in links]), "aarch64", SHA)
        below = tarfile.TarInfo("agterm-stage/lib/x/s/payload")
        with self.assertRaises(ValueError):
            promotion.validate_payload(payload(extra=[self.link("/lib/x/s", ".."), below]), "aarch64", SHA)

    def test_rejects_members_and_hardlinks_through_links(self):
        stage_link = self.link("share/out", "../lib")
        below = tarfile.TarInfo("agterm-stage/share/out/planted")
        through = self.link("bin/copy", "agterm-stage/share/out/../../x", tarfile.LNKTYPE)
        for members in ([stage_link, below], [stage_link, through]):
            with self.subTest(members=[member.name for member in members]), self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=members), "aarch64", SHA)

    def test_rejects_duplicate_members_and_devices(self):
        duplicate = tarfile.TarInfo("agterm-stage/share/agterm/COMMIT")
        device = tarfile.TarInfo("agterm-stage/device")
        device.type = tarfile.CHRTYPE
        for member in (duplicate, device):
            with self.assertRaises(ValueError):
                promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    def test_rejects_alternate_spellings_of_one_member(self):
        cases = (("plain duplicate", tarfile.TarInfo("agterm-stage/bin/zmx"), "Duplicate tar entry"),
                 ("symlink", self.link("bin/zmx/", "agterm-linux.bin"), "Trailing slash on a non-directory"),
                 ("regular file", tarfile.TarInfo("agterm-stage/share/agterm/COMMIT/"),
                  "Trailing slash on a non-directory"))
        for kind, member, reason in cases:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, reason):
                promotion.validate_payload(payload(extra=member), "aarch64", SHA)

    def test_ci_gates_cannot_be_skipped(self):
        names = ["Installer and scripts", "SwiftLint (Linux)", "CI"]
        for arch in promotion.ARCHITECTURES:
            names += [f"Core tests ({arch})", f"GTK build and tests ({arch})"]
        steps = [{"name": name, "conclusion": "success"} for name in (
            "swift test (agterm-linux)", "swift build Release/WMO (agterm-linux)",
            "Validate staged Linux payload", "GTK AT-SPI smoke tests", "Upload tested runtime payload")]
        jobs = [{"name": name, "conclusion": "success", "steps": copy.deepcopy(steps)} for name in names]
        promotion.validate_jobs(jobs)
        for index in range(len(jobs)):
            failed = copy.deepcopy(jobs)
            failed[index]["conclusion"] = "skipped"
            with self.subTest(job=failed[index]["name"]), self.assertRaises(ValueError):
                promotion.validate_jobs(failed)
        jobs[-1]["steps"][3]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            promotion.validate_jobs(jobs)

    def test_artifact_digest_and_zip_identity(self):
        data = io.BytesIO()
        original = payload()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("agterm-omarchy-aarch64.tar.gz", original)
        zipped = data.getvalue()
        artifact = {"id": 123, "expired": False, "workflow_run": {"head_sha": SHA},
                    "digest": "sha256:" + hashlib.sha256(zipped).hexdigest()}
        with patch.object(promotion.subprocess, "check_output", return_value=zipped):
            name, actual = promotion.download_artifact(artifact, "aarch64", SHA)
            self.assertEqual(name, "agterm-omarchy-aarch64.tar.gz")
            self.assertEqual(actual, original)
            for key, value in (("expired", True), ("digest", "sha256:" + "0" * 64),
                               ("workflow_run", {"head_sha": "b" * 40})):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    promotion.download_artifact(dict(artifact, **{key: value}), "aarch64", SHA)
            with self.assertRaises(ValueError):
                promotion.download_artifact(artifact, "x86_64", SHA)


def workflow_jobs(text):
    """Split a workflow into its top-level jobs by their two-space-indented keys."""
    body = text[text.index("\njobs:\n"):]
    parts = re.split(r"^  ([a-z][a-z0-9-]*):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def zipped(arch, sha=SHA):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"agterm-omarchy-{arch}.tar.gz", payload(arch, sha))
    return output.getvalue()


class FakeGitHub:
    """Answers the read-only API calls a promotion makes; any write or unknown path fails the test."""

    def __init__(self, run_id=7, sha=SHA, branch="main", existing=(), failing=()):
        self.run_id, self.sha, self.existing, self.failing = run_id, sha, set(existing), set(failing)
        self.calls = []
        self.zips = {arch: zipped(arch, sha) for arch in promotion.ARCHITECTURES}
        steps = [{"name": name, "conclusion": "success"} for name in promotion.REQUIRED_GTK_STEPS]
        names = list(promotion.REQUIRED_JOBS) + [name.format(arch=arch) for arch in promotion.ARCHITECTURES
                                                 for name in promotion.REQUIRED_ARCH_JOBS]
        self.jobs = [{"name": name, "conclusion": "success", "steps": steps} for name in names]
        self.run = {"id": run_id, "repository": {"full_name": promotion.REPOSITORY},
                    "head_repository": {"full_name": promotion.REPOSITORY}, "path": promotion.WORKFLOW,
                    "workflow_id": 42, "name": "Omarchy CI", "status": "completed", "conclusion": "success",
                    "event": "push", "head_branch": branch, "head_sha": sha, "run_attempt": 1,
                    "html_url": f"https://github.com/{promotion.REPOSITORY}/actions/runs/{run_id}",
                    "run_started_at": "2026-11-01T00:00:00Z", "created_at": "2026-11-01T00:00:00Z"}
        self.artifacts = [{"id": 100 + index, "name": f"agterm-omarchy-{arch}-{sha}", "expired": False,
                           "workflow_run": {"id": run_id, "head_sha": sha}, "created_at": "2026-11-01T00:10:00Z",
                           "digest": "sha256:" + hashlib.sha256(self.zips[arch]).hexdigest()}
                          for index, arch in enumerate(promotion.ARCHITECTURES)]
        self.runs = {run_id: (self.run, self.jobs, self.artifacts)}
        self.listed = [self.run]

    def add_run(self, run_id, created_at, jobs=None, artifacts=None, **changes):
        """Another run at the same head, listed newest first; its artifacts default to fresh copies of the base run's."""
        run = dict(self.run, id=run_id, created_at=created_at, run_started_at=created_at,
                   html_url=f"https://github.com/{promotion.REPOSITORY}/actions/runs/{run_id}")
        run.update(changes)
        if artifacts is None:
            artifacts = [dict(item, workflow_run={"id": run_id, "head_sha": self.sha}, created_at=created_at)
                         for item in self.artifacts]
        self.runs[run_id] = (run, copy.deepcopy(self.jobs) if jobs is None else jobs, artifacts)
        self.listed.insert(0, run)
        return run

    def answer(self, path):
        base = f"repos/{promotion.REPOSITORY}/"
        if not path.startswith(base):
            raise AssertionError(f"Unexpected API call {path}")
        path = path[len(base):]
        if path == "actions/workflows/ci.yml":
            return {"id": 42}
        if path.startswith("git/ref/heads/"):
            return {"object": {"sha": self.sha}}
        if path.startswith("actions/workflows/ci.yml/runs?"):
            return [{"workflow_runs": self.listed if "branch=main&" in path else []}]
        for run_id, (run, jobs, artifacts) in self.runs.items():
            if path == f"actions/runs/{run_id}":
                return run
            if path.startswith(f"actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?"):
                return [{"jobs": jobs}]
            if path.startswith(f"actions/runs/{run_id}/artifacts?"):
                return [{"artifacts": artifacts}]
        for index, arch in enumerate(promotion.ARCHITECTURES):
            if path == f"actions/artifacts/{100 + index}/zip":
                return self.zips[arch]
        raise AssertionError(f"Unexpected API call {path}")

    def check_output(self, command):
        self.calls.append(command)
        if command[:2] != ["gh", "api"] or any(flag in command for flag in ("--method", "-X", "-f", "-F")):
            raise AssertionError(f"Promotion attempted a write: {command}")
        result = self.answer(command[2])
        if isinstance(result, bytes):
            return result
        if isinstance(result, list) and "--paginate" not in command:
            # Listings are answered as slurped pages; an unpaginated call reads only the first.
            result = result[0]
        return json.dumps(result).encode()

    def run_command(self, command, **kwargs):
        self.calls.append(command)
        path = command[2].split(f"repos/{promotion.REPOSITORY}/", 1)[1]
        if path in self.failing:
            return subprocess.CompletedProcess(command, 1, "", "gh: Bad credentials (HTTP 401)")
        found = path in self.existing
        return subprocess.CompletedProcess(command, 0 if found else 1, "", "" if found else "gh: Not Found (HTTP 404)")

    def patches(self):
        return (patch.object(promotion.subprocess, "check_output", side_effect=self.check_output),
                patch.object(promotion.subprocess, "run", side_effect=self.run_command))


class DryRunTests(unittest.TestCase):
    def prepare(self, github, output, env=None):
        first, second = github.patches()
        argv = ["prepare", str(github.run_id), github.sha, str(output)]
        environ = {key: value for key, value in os.environ.items() if key != "GITHUB_OUTPUT"} | (env or {})
        with first, second, patch("sys.argv", argv), patch.dict(os.environ, environ, clear=True):
            promotion.main()

    def test_dry_run_executes_every_check_and_writes_only_local_files(self):
        github = FakeGitHub()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "promotion"
            outputs = Path(temp) / "github-output"
            self.prepare(github, output, {"GITHUB_OUTPUT": str(outputs)})
            tag = f"omarchy-dev-{SHA[:12]}-run7"
            self.assertEqual(outputs.read_text(), f"tag={tag}\nsha={SHA}\n")
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["SHA256SUMS", "agterm-omarchy-aarch64.tar.gz", "agterm-omarchy-x86_64.tar.gz",
                              "promotion.json", "release-notes.md"])
            check = subprocess.run(["shasum", "-a", "256", "-c", "SHA256SUMS"] if shutil.which("shasum")
                                   else ["sha256sum", "--check", "SHA256SUMS"], cwd=output, capture_output=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            notes = (output / "release-notes.md").read_text()
            self.assertIn(tag, notes)
            self.assertIn(github.run["html_url"], notes)
            self.assertIn(f"/commit/{SHA}", notes)
            evidence = json.loads((output / "promotion.json").read_text())
            self.assertEqual(evidence["source_commit"], SHA)
            self.assertEqual([item["filename"] for item in evidence["artifacts"]],
                             [f"agterm-omarchy-{arch}.tar.gz" for arch in promotion.ARCHITECTURES])
        paths = [call[2] for call in github.calls]
        for expected in ("actions/workflows/ci.yml", "/jobs", "/artifacts", "/zip", f"git/ref/tags/{tag}",
                         f"releases/tags/{tag}"):
            self.assertTrue(any(expected in path for path in paths), f"{expected} was not checked")

    def test_dry_run_fails_where_publication_would_on_an_existing_tag_or_release(self):
        tag = f"omarchy-dev-{SHA[:12]}-run7"
        for existing in (f"git/ref/tags/{tag}", f"releases/tags/{tag}"):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "promotion"
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.prepare(FakeGitHub(existing=[existing]), output)
                self.assertFalse(output.exists())

    def test_absent_never_treats_other_failures_as_missing(self):
        for code, stderr, expected in ((0, "", False), (1, "gh: Not Found (HTTP 404)", True)):
            result = subprocess.CompletedProcess([], code, "", stderr)
            with patch.object(promotion.subprocess, "run", return_value=result):
                self.assertIs(promotion.absent("git/ref/tags/x"), expected)
        result = subprocess.CompletedProcess([], 1, "", "gh: Bad credentials (HTTP 401)")
        with patch.object(promotion.subprocess, "run", return_value=result), self.assertRaises(ValueError):
            promotion.absent("git/ref/tags/x")

    def test_promotion_tag_grammar(self):
        self.assertEqual(promotion.promotion_tag(SHA, 41532553668), f"omarchy-dev-{SHA[:12]}-run41532553668")
        for sha, run_id in (("A" * 40, 1), ("g" * 40, 1), ("a" * 11, 1), (SHA, 0), (SHA, -1)):
            with self.subTest(sha=sha, run_id=run_id), self.assertRaises(ValueError):
                promotion.promotion_tag(sha, run_id)

    def discover(self, github):
        first, second = github.patches()
        with first, second:
            return promotion.discover()

    def test_discover_picks_the_newest_trusted_run_with_both_artifacts(self):
        github = FakeGitHub()
        github.add_run(8, "2026-11-02T00:00:00Z")
        github.add_run(9, "2026-11-03T00:00:00Z", event="pull_request")
        self.assertEqual(self.discover(github)["id"], 8)
        github.listed[:] = [github.runs[9][0]]
        self.assertIsNone(self.discover(github))

    def test_discover_skips_expired_artifacts_but_accepts_the_same_fresh_run(self):
        for index, arch in enumerate(promotion.ARCHITECTURES):
            with self.subTest(arch=arch):
                github = FakeGitHub()
                # add_run gives both artifacts this attempt's timestamp, so expiry is
                # the only failing gate (not the separate stale-attempt check).
                github.add_run(8, "2026-11-02T00:00:00Z")
                artifact = github.runs[8][2][index]
                artifact["expired"] = True
                self.assertEqual(self.discover(github)["id"], 7)
                with tempfile.TemporaryDirectory() as temp:
                    output = Path(temp) / "promotion"
                    with self.assertRaisesRegex(ValueError, "Expired artifact"):
                        self.prepare(FakeGitHubFor(github, 8), output)
                    self.assertFalse(output.exists())
                artifact["expired"] = False
                self.assertEqual(self.discover(github)["id"], 8)
                with tempfile.TemporaryDirectory() as temp:
                    self.prepare(FakeGitHubFor(github, 8), Path(temp) / "promotion")

    def test_prepare_rejects_a_different_run_id_before_following_its_metadata(self):
        github = FakeGitHub()
        github.run["id"] = 8
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "promotion"
            with self.assertRaisesRegex(ValueError, "Requested run ID mismatch"):
                self.prepare(github, output)
            self.assertFalse(output.exists())
        self.assertEqual(github.calls, [["gh", "api", f"repos/{promotion.REPOSITORY}/actions/runs/7"]])

    def test_prepare_rechecks_requested_run_id_after_downloading(self):
        github = FakeGitHub()
        answer = github.answer
        calls = 0

        def changed_run(path):
            nonlocal calls
            result = answer(path)
            if path == f"repos/{promotion.REPOSITORY}/actions/runs/7":
                calls += 1
                if calls == 2:
                    return dict(result, id=8)
            return result

        with patch.object(github, "answer", side_effect=changed_run), tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "promotion"
            outputs = Path(temp) / "github-output"
            with self.assertRaisesRegex(ValueError, "Requested run ID mismatch"):
                self.prepare(github, output, {"GITHUB_OUTPUT": str(outputs)})
            self.assertEqual(calls, 2)
            self.assertFalse((output / "promotion.json").exists())
            self.assertFalse((output / "SHA256SUMS").exists())
            self.assertFalse(outputs.exists())

    def test_discover_skips_a_run_whose_tag_or_release_exists(self):
        for kind in ("git/ref/tags", "releases/tags"):
            with self.subTest(kind=kind):
                github = FakeGitHub(existing=[f"{kind}/omarchy-dev-{SHA[:12]}-run8"])
                github.add_run(8, "2026-11-02T00:00:00Z")
                found = self.discover(github)
                self.assertEqual(found["id"], 7)
                with tempfile.TemporaryDirectory() as temp:
                    self.prepare(github, Path(temp) / "promotion")
                github.existing.add(f"{kind}/omarchy-dev-{SHA[:12]}-run7")
                self.assertIsNone(self.discover(github))

    def test_discover_skips_a_run_whose_gates_main_would_reject(self):
        github = FakeGitHub()
        jobs = copy.deepcopy(github.jobs)
        gtk = next(job for job in jobs if job["name"].startswith("GTK build"))
        gtk["steps"] = [dict(step, conclusion="skipped") if step["name"] == "GTK AT-SPI smoke tests" else step
                        for step in gtk["steps"]]
        github.add_run(8, "2026-11-02T00:00:00Z", jobs=jobs)
        self.assertEqual(self.discover(github)["id"], 7)
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "GTK AT-SPI smoke tests"):
            self.prepare(FakeGitHubFor(github, 8), Path(temp) / "promotion")

    def test_discover_skips_a_rerun_attempt_whose_artifacts_predate_it(self):
        github = FakeGitHub()
        stale = [dict(item, workflow_run={"id": 8, "head_sha": SHA}, created_at="2026-11-02T00:10:00Z")
                 for item in github.artifacts]
        github.add_run(8, "2026-11-02T00:00:00Z", artifacts=stale, run_attempt=2, run_started_at="2026-11-02T01:00:00Z")
        self.assertEqual(self.discover(github)["id"], 7)
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "predates this run attempt"):
            self.prepare(FakeGitHubFor(github, 8), Path(temp) / "promotion")

    def test_discover_reports_a_failed_tag_lookup_instead_of_skipping_the_run(self):
        github = FakeGitHub(failing=[f"git/ref/tags/omarchy-dev-{SHA[:12]}-run7"])
        with self.assertRaisesRegex(ValueError, "Could not check"):
            self.discover(github)


def release_zip(arch, sha=SHA):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"agterm-omarchy-{arch}.tar.gz", release_fixture.archive(arch, sha))
    return output.getvalue()


class ReleaseGitHub(FakeGitHub):
    """The fake with archives built like the fixed stage, plus releases, tags and main ancestry."""

    def __init__(self, releases=(), ahead=True, ci=None, **kwargs):
        super().__init__(**kwargs)
        for job in self.jobs:
            if job["name"].startswith("GTK build"):
                job["steps"] = job["steps"] + [{"name": name, "conclusion": "success"}
                                               for name in promotion.RELEASE_GTK_STEPS]
        self.zips = {arch: release_zip(arch, self.sha) for arch in promotion.ARCHITECTURES}
        for artifact, arch in zip(self.artifacts, promotion.ARCHITECTURES):
            artifact["digest"] = "sha256:" + hashlib.sha256(self.zips[arch]).hexdigest()
        self.releases, self.ahead = list(releases), ahead
        self.ci = CI_WORKFLOW.read_text() if ci is None else ci

    def answer(self, path):
        base = f"repos/{promotion.REPOSITORY}/"
        short = path[len(base):]
        if short.startswith("releases?"):
            return [self.releases]
        if short.startswith("compare/"):
            return {"status": "ahead" if self.ahead else "diverged"}
        if short.startswith("git/ref/heads/main") and not self.ahead:
            return {"object": {"sha": "f" * 40}}
        if short == f"contents/{promotion.WORKFLOW}?ref={self.sha}":
            return {"encoding": "base64", "content": base64.b64encode(self.ci.encode()).decode()}
        return super().answer(path)


def published_release(version, prerelease=False, draft=False):
    return {"tag_name": f"omarchy-v{version}", "prerelease": prerelease, "draft": draft}


class ReleaseDryRunTests(unittest.TestCase):
    def prepare(self, github, output, *extra, env=None):
        first, second = github.patches()
        argv = ["prepare", str(github.run_id), github.sha, str(output), *extra]
        environ = {key: value for key, value in os.environ.items()
                   if key not in ("GITHUB_OUTPUT", "GITHUB_REF")} | (env or {})
        with first, second, patch("sys.argv", argv), patch.dict(os.environ, environ, clear=True):
            promotion.main()

    def test_dispatch_dry_run_builds_both_versioned_assets(self):
        github = ReleaseGitHub(releases=[published_release("0.43.0.1", prerelease=True)])
        with tempfile.TemporaryDirectory() as temp:
            output, outputs = Path(temp) / "promotion", Path(temp) / "github-output"
            self.prepare(github, output, "--release", "0.43.0.1", env={"GITHUB_OUTPUT": str(outputs)})
            self.assertEqual(outputs.read_text(), f"tag=omarchy-v0.43.0.1\nsha={SHA}\nversion=0.43.0.1\nlatest=true\n")
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["SHA256SUMS", "agterm-omarchy-0.43.0.1-aarch64.tar.gz",
                              "agterm-omarchy-0.43.0.1-x86_64.tar.gz", "promotion.json", "release-notes.md"])
            check = subprocess.run(["shasum", "-a", "256", "-c", "SHA256SUMS"] if shutil.which("shasum")
                                   else ["sha256sum", "--check", "SHA256SUMS"], cwd=output, capture_output=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            evidence = json.loads((output / "promotion.json").read_text())
            self.assertEqual((evidence["tag"], evidence["version"], evidence["latest"]), ("omarchy-v0.43.0.1", "0.43.0.1", True))
            for item, artifact in zip(evidence["releases"], evidence["artifacts"]):
                self.assertEqual(item["tested_archive_sha256"], artifact["sha256"])
                self.assertEqual(item["changed_members"], ["agterm-stage/share/agterm/VERSION"])
            notes = (output / "release-notes.md").read_text()
            self.assertIn("--source-ref refs/heads/main", notes)
            self.assertIn("agterm-omarchy-0.43.0.1-aarch64.tar.gz", notes)
        paths = [call[2] for call in github.calls]
        for expected in ("git/ref/tags/omarchy-v0.43.0.1", "releases/tags/omarchy-v0.43.0.1", "releases?"):
            self.assertTrue(any(expected in path for path in paths), f"{expected} was not checked")

    def test_latest_follows_the_highest_published_version(self):
        self.assertTrue(promotion.is_latest("0.43.0.10", ["0.43.0.9", "0.43.0.2"]))
        self.assertFalse(promotion.is_latest("0.43.0.3", ["0.43.0.5"]))
        self.assertTrue(promotion.is_latest("0.43.0.1", []))
        self.assertTrue(promotion.is_latest("0.44.0.1", ["0.43.0.12"]))
        github = ReleaseGitHub(releases=[published_release("0.43.0.5"), published_release("0.43.0.9", draft=True)])
        with tempfile.TemporaryDirectory() as temp:
            outputs = Path(temp) / "github-output"
            self.prepare(github, Path(temp) / "promotion", "--release", "0.43.0.3", env={"GITHUB_OUTPUT": str(outputs)})
            self.assertIn("latest=false\n", outputs.read_text())

    def test_next_version_skips_prereleases_drafts_and_dev_tags(self):
        github = ReleaseGitHub(releases=[published_release("0.43.0.1"), published_release("0.43.0.2"),
                                         published_release("0.43.0.9", prerelease=True),
                                         published_release("0.43.0.8", draft=True),
                                         {"tag_name": f"omarchy-dev-{SHA[:12]}-run7", "prerelease": True, "draft": False},
                                         published_release("0.40.0.4")])
        first, second = github.patches()
        with first, second:
            self.assertEqual(promotion.next_version(promotion.release_module()), "0.43.0.3")

    def test_dispatch_refuses_an_existing_tag_or_release(self):
        for existing in ("git/ref/tags/omarchy-v0.43.0.1", "releases/tags/omarchy-v0.43.0.1"):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.prepare(ReleaseGitHub(existing=[existing]), Path(temp) / "promotion", "--release", "0.43.0.1")

    def test_rejects_old_format_archives_untrusted_branches_and_foreign_commits(self):
        old_format = ReleaseGitHub()
        old_format.zips = {arch: zipped(arch) for arch in promotion.ARCHITECTURES}
        for artifact, arch in zip(old_format.artifacts, promotion.ARCHITECTURES):
            artifact["digest"] = "sha256:" + hashlib.sha256(old_format.zips[arch]).hexdigest()
        cases = ((old_format, "RUNPATH is not"), (ReleaseGitHub(branch="omarchy"), "only from main"),
                 (ReleaseGitHub(ahead=False), "not a commit on main"))
        for github, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, error):
                    self.prepare(github, Path(temp) / "promotion", "--release", "0.43.0.1")
                self.assertFalse((Path(temp) / "promotion" / "SHA256SUMS").exists())

    def test_a_release_needs_the_native_release_rehearsal_on_both_arches(self):
        for arch in promotion.ARCHITECTURES:
            github = ReleaseGitHub()
            gtk = next(job for job in github.jobs if job["name"] == f"GTK build and tests ({arch})")
            gtk["steps"] = [step for step in gtk["steps"] if step["name"] != "Rehearse versioned release archive"]
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "Rehearse versioned release archive"):
                    self.prepare(github, Path(temp) / "promotion", "--release", "0.43.0.1")
                self.prepare(github, Path(temp) / "dev")

    def test_rejects_malformed_versions_before_any_api_call(self):
        for version in ("0.43.0.0", "0.43.0.1rc1", "0.43.0.01", "v0.43.0.1", "0.43.0.1-1"):
            github = ReleaseGitHub()
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):
                    self.prepare(github, Path(temp) / "promotion", "--release", version)
                self.assertEqual(github.calls, [])

    def test_the_upstream_base_comes_from_the_source_commit(self):
        for version in ("0.43.1", "0.43.1.1"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "is not 0.43.0.N"):
                    self.prepare(ReleaseGitHub(), Path(temp) / "promotion", "--release", version)
        moved = CI_WORKFLOW.read_text().replace('UPSTREAM_BASE_TAG: "v0.43.0"', 'UPSTREAM_BASE_TAG: "v0.44.0"')
        with tempfile.TemporaryDirectory() as temp:
            self.prepare(ReleaseGitHub(ci=moved), Path(temp) / "next", "--release", "0.44.0.1")
            with self.assertRaisesRegex(ValueError, "is not 0.44.0.N"):
                self.prepare(ReleaseGitHub(ci=moved), Path(temp) / "old", "--release", "0.43.0.1")

    def test_the_requested_run_id_must_be_the_run_the_api_returns(self):
        for extra in ((), ("--release", "0.43.0.1")):
            github = ReleaseGitHub()
            github.runs[7] = (dict(github.run, id=8),) + github.runs[7][1:]
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "Requested run ID mismatch"):
                    self.prepare(github, Path(temp) / "promotion", *extra)

    def test_tagged_is_not_an_option(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(SystemExit):
            self.prepare(ReleaseGitHub(), Path(temp) / "promotion", "--release", "0.43.0.1", "--tagged")

    def test_releasable_discovery_falls_back_past_a_head_run_release_mode_rejects(self):
        github = ReleaseGitHub()
        head = github.add_run(8, "2026-11-02T00:00:00Z")
        gtk = next(job for job in github.runs[8][1] if job["name"].startswith("GTK build"))
        gtk["steps"] = [step for step in gtk["steps"] if step["name"] != "Rehearse versioned release archive"]
        github.add_run(9, "2026-11-03T00:00:00Z", head_branch="omarchy")
        first, second = github.patches()
        with first, second:
            self.assertEqual(promotion.discover_releasable()["id"], 7)
            github.listed[:] = [head]
            self.assertIsNone(promotion.discover_releasable())

    def test_discover_commit_finds_the_green_main_run_at_that_commit(self):
        github = ReleaseGitHub()
        github.add_run(8, "2026-11-02T00:00:00Z", event="pull_request")
        first, second = github.patches()
        with first, second:
            self.assertEqual(promotion.discover_commit(SHA)["id"], 7)
        github = ReleaseGitHub(ahead=False)
        first, second = github.patches()
        with first, second, self.assertRaisesRegex(ValueError, "not a commit on main"):
            promotion.discover_commit(SHA)


def FakeGitHubFor(github, run_id):
    """The same fake, prepared against another of its runs."""
    view = copy.copy(github)
    view.run_id = run_id
    view.run = github.runs[run_id][0]
    return view


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = PROMOTION_WORKFLOW.read_text()
        self.jobs = workflow_jobs(self.text)

    def test_dispatch_defaults_to_a_dry_run(self):
        inputs = self.text[self.text.index("      dry_run:\n"):self.text.index("\npermissions:")]
        self.assertIn("type: boolean", inputs)
        self.assertIn("default: true", inputs)

    def test_only_an_explicit_non_dry_dispatch_from_main_can_publish(self):
        self.assertEqual(set(self.jobs), {"test", "prepare", "publish"})
        self.assertIn("    if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' "
                      "&& inputs.dry_run == false\n", self.jobs["publish"])
        triggers = self.text[self.text.index("\non:\n"):self.text.index("\npermissions:")]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", triggers, re.M), ["pull_request", "workflow_dispatch"])
        for push in ("push:", "== 'push'", "refs/tags/omarchy-v"):
            self.assertNotIn(push, self.text)
        self.assertNotIn("--tagged", self.text)
        for name in ("test", "prepare"):
            job = self.jobs[name]
            for privileged in ("id-token: write", "contents: write", "attestations: write", "actions/attest",
                               "gh release", "git/refs", "--method"):
                with self.subTest(job=name, privileged=privileged):
                    self.assertNotIn(privileged, job)

    def test_workflow_token_is_read_only_unless_a_job_widens_it(self):
        top = self.text[:self.text.index("\njobs:\n")]
        self.assertIn("\npermissions:\n  contents: read\n\n", top)
        self.assertEqual(top.count("permissions:"), 1)

    def test_attestation_verification_pins_the_source_runner_and_both_predicates(self):
        job = self.jobs["publish"]
        verify = job[job.index("      - name: Verify the new attestations before creating any tag\n"):
                     job.index("      - name: Create new tag and prerelease")]
        self.assertIn('for predicate in https://slsa.dev/provenance/v1 '
                      '"https://github.com/$GH_REPO/attestations/promotion/v1"; do', verify)
        for flag in ('--source-digest "$GITHUB_SHA"', "--deny-self-hosted-runners",
                     '--predicate-type "$predicate"', "--source-ref refs/heads/main"):
            with self.subTest(flag=flag):
                self.assertIn(flag, verify)

    def test_only_a_publishing_dispatch_uploads_the_release_payload(self):
        job = self.jobs["prepare"]
        upload = job[job.rindex("      - uses: actions/upload-artifact@"):]
        self.assertIn("name: verified-prerelease-", upload)
        self.assertIn("        if: github.event_name == 'workflow_dispatch' && inputs.dry_run == false\n", upload)
        self.assertEqual(job.count("verified-prerelease-"), 1)

    def test_a_dry_run_without_a_source_says_it_checked_nothing(self):
        job = self.jobs["prepare"]
        self.assertIn("        if: steps.source.outputs.run != ''\n", job)
        self.assertIn('echo "::warning::No promotable source run: promotion checks were not exercised"', job)

    def test_publication_verifies_attestations_before_creating_the_tag(self):
        job = self.jobs["publish"]
        verify = job.index("gh attestation verify")
        self.assertLess(job.rindex("actions/attest@"), verify)
        self.assertLess(verify, job.index('gh api --method POST "repos/$GH_REPO/git/refs"'))
        self.assertIn("--signer-workflow \"$GH_REPO/.github/workflows/prerelease-omarchy.yml\"", job)
        self.assertIn("--source-ref refs/heads/main", job)
        self.assertIn("--prerelease --latest=false", job)

    def test_versioned_release_is_a_non_prerelease_with_latest_by_version(self):
        job = self.jobs["publish"]
        step = job[job.index("      - name: Create the versioned release without replacing anything\n"):]
        self.assertIn("        if: needs.prepare.outputs.version != ''\n", step)
        self.assertIn('--latest="$LATEST"', step)
        self.assertNotIn("--prerelease", step)
        self.assertIn('gh api --method POST "repos/$GH_REPO/git/refs"', step)
        self.assertLess(job.index("gh attestation verify"), job.index(step))
        dev = job[job.index("      - name: Create new tag and prerelease"):job.index(step)]
        self.assertIn("        if: needs.prepare.outputs.version == ''\n", dev)

    def test_dispatch_selects_the_release_mode_and_prs_dry_run_main_sources(self):
        job = self.jobs["prepare"]
        self.assertIn("VERSION: ${{ inputs.version }}", job)
        self.assertIn('[[ -z "$VERSION" ]] || mode=(--release "$VERSION")', job)
        step = job[job.index("      - name: Dry-run the next versioned release on the newest main source\n"):]
        step = step[:step.index("\n      - name: ")]
        self.assertIn("prepare-omarchy-prerelease.py --discover --releasable", step)
        self.assertIn('--release "$version"', step)
        self.assertNotIn("steps.source.outputs", step)

    def test_dry_run_runs_the_same_validation_and_checksum_steps(self):
        job = self.jobs["prepare"]
        self.assertIn("if: github.event_name == 'pull_request' || github.ref == 'refs/heads/main'", job)
        self.assertIn('python3 scripts/prepare-omarchy-prerelease.py --discover', job)
        self.assertIn('python3 scripts/prepare-omarchy-prerelease.py "$SOURCE_RUN" "$SOURCE_SHA"', job)
        self.assertIn("sha256sum --check SHA256SUMS", job)
        self.assertIn("release-notes.md", job)

    def test_legacy_linux_release_is_retired_here_and_shares_the_upstream_base(self):
        legacy = (REPO / ".github/workflows/release-linux.yml").read_text()
        triggers = legacy[legacy.index("\non:\n"):legacy.index("\nconcurrency:")]
        self.assertNotIn("push:", triggers)
        jobs = workflow_jobs(legacy)
        self.assertEqual(set(jobs), {"sbom", "release-linux"})
        for name, body in jobs.items():
            with self.subTest(job=name):
                self.assertIn("    if: github.repository != 'alexandru-savinov/agterm-omarchy'\n", body)
        ci = CI_WORKFLOW.read_text()
        for name in ("UPSTREAM_BASE_TAG", "UPSTREAM_BASE_COMMIT"):
            pin = re.compile(rf'^  {name}: "([^"]+)"$', re.M)
            with self.subTest(name=name):
                self.assertEqual(pin.search(legacy)[1], pin.search(ci)[1])

    def test_required_gates_exist_in_ci(self):
        ci = CI_WORKFLOW.read_text()
        self.assertIn("name: Omarchy CI\n", ci)
        jobs = workflow_jobs(ci)
        names = {job: re.search(r"^    name: (.+)$", body, re.M).group(1) for job, body in jobs.items()}
        build = next(body for job, body in jobs.items() if names[job] == "GTK build and tests (${{ matrix.arch }})")
        for arch in promotion.ARCHITECTURES:
            self.assertIn(f"arch: {arch}", build)
        for name in promotion.REQUIRED_JOBS + promotion.REQUIRED_ARCH_JOBS:
            with self.subTest(job=name):
                self.assertIn(name.format(arch="${{ matrix.arch }}"), names.values())
        for step in promotion.REQUIRED_GTK_STEPS + promotion.RELEASE_GTK_STEPS:
            with self.subTest(step=step):
                self.assertEqual(build.count(f"      - name: {step}\n"), 1)


if __name__ == "__main__":
    unittest.main()
