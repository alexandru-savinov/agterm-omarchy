#!/usr/bin/env python3
import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("upstream_sync", Path(__file__).resolve().parents[1] / "upstream-sync.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
FIELDS_SPEC = importlib.util.spec_from_file_location("pr_fields", Path(__file__).resolve().parents[1] / "github/pr_fields.py")
pr_fields = importlib.util.module_from_spec(FIELDS_SPEC)
FIELDS_SPEC.loader.exec_module(pr_fields)
RUN = "https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/1"
WORKFLOW = (Path(__file__).resolve().parents[2] / ".github/workflows/upstream-sync.yml").read_text(encoding="utf-8")
PUBLISH = Path(__file__).resolve().parents[1] / "upstream-sync-publish.sh"

TAGS = {"v0.41.1": "a" * 40, "v0.42.3": "b" * 40, "v0.43.0": "c" * 40, "v0.9.9": "d" * 40}


def contains(*commits):
    return lambda commit: commit in commits


def pr(number, head="x", labels=(), cross=False):
    return {"number": number, "headRefName": head, "labels": [{"name": name} for name in labels],
            "isCrossRepository": cross}


def text(conflicts=(), github_changes=(), head="2" * 40):
    return sync.pr_text(tag="v0.43.0", commit="c" * 40, base_sha="1" * 40, head_sha=head, run_url=RUN,
                        conflicts=list(conflicts), github_changes=list(github_changes))


def decide(**overrides):
    args = {"paused_value": "", "incidents": [], "prs": [], "tags": TAGS, "is_ancestor": contains("a" * 40),
            "remote_branches": set(), "override": ""}
    args.update(overrides)
    return sync.decide(**args)


class SelectionTests(unittest.TestCase):
    def test_newest_release_not_in_main_is_selected(self):
        self.assertEqual(sync.select(TAGS, contains("a" * 40))[:2], ("v0.43.0", "c" * 40))

    def test_versions_sort_numerically_not_lexically(self):
        tags = {"v0.9.0": "1" * 40, "v0.10.0": "2" * 40, "v0.10.10": "3" * 40, "v0.10.9": "4" * 40}
        self.assertEqual(sync.select(tags, contains())[0], "v0.10.10")

    def test_nothing_to_do_when_newest_release_is_an_ancestor(self):
        tag, commit, reason = sync.select(TAGS, contains("c" * 40))
        self.assertIsNone(tag)
        self.assertEqual(commit, "c" * 40)
        self.assertIn("already contains v0.43.0", reason)

    def test_older_non_ancestor_release_is_never_synced_backwards(self):
        self.assertIsNone(sync.select(TAGS, contains("c" * 40, "a" * 40))[0])

    def test_non_release_tags_are_ignored(self):
        tags = {**TAGS, "v0.44.0-rc1": "e" * 40, "v1.0": "f" * 40, "v01.0.0": "9" * 40, "omarchy-v0.45.0.1": "8" * 40}
        self.assertEqual(sync.select(tags, contains())[0], "v0.43.0")

    def test_override_selects_an_older_release(self):
        self.assertEqual(sync.select(TAGS, contains(), "v0.42.3")[:2], ("v0.42.3", "b" * 40))

    def test_override_already_in_main_is_nothing_to_do(self):
        self.assertIsNone(sync.select(TAGS, contains("b" * 40), "v0.42.3")[0])

    def test_override_must_be_a_fetched_release_tag(self):
        for bad in ("v0.44.0", "main", "v0.43.0-rc1", "refs/tags/v0.43.0"):
            with self.subTest(bad=bad), self.assertRaises(sync.SyncError):
                sync.select(TAGS, contains(), bad)

    def test_no_release_tags_fails_instead_of_skipping(self):
        with self.assertRaises(sync.SyncError):
            sync.select({"latest": "a" * 40}, contains())


class GateTests(unittest.TestCase):
    def test_clean_state_syncs_the_selected_tag(self):
        plan = decide()
        self.assertEqual((plan["action"], plan["tag"], plan["branch"]), ("sync", "v0.43.0", "sync/umputun-v0.43.0"))

    def test_pause_wins_over_every_other_gate(self):
        for value in ("true", "TRUE", " true\n"):
            with self.subTest(value=value):
                plan = decide(paused_value=value, prs=[pr(9, labels=["upstream-sync"])],
                              remote_branches={"sync/umputun-v0.43.0"})
                self.assertEqual((plan["action"], plan["reason"], plan["tag"]), ("skip", "SYNC_PAUSED is true", ""))

    def test_paused_run_selects_nothing(self):
        def unreachable(_):
            raise AssertionError("a paused run must not inspect history")
        for pause in ({"paused_value": "true"}, {"incidents": [{"number": 5}]}):
            with self.subTest(pause=pause):
                self.assertEqual(decide(**pause, is_ancestor=unreachable, tags={})["action"], "skip")

    def test_open_release_incident_pauses_like_the_variable(self):
        plan = decide(incidents=[{"number": 5}, {"number": 8}], remote_branches={"sync/umputun-v0.43.0"})
        self.assertEqual((plan["action"], plan["tag"]), ("skip", ""))
        self.assertEqual(plan["reason"], "an open release-incident issue pauses syncing: #5, #8")

    def test_variable_and_incident_together_still_pause(self):
        self.assertEqual(decide(paused_value="true", incidents=[{"number": 5}])["action"], "skip")

    def test_malformed_incident_listing_fails_closed(self):
        for paused_value in ("", "true"):
            with self.subTest(paused=paused_value), self.assertRaises(sync.SyncError):
                decide(paused_value=paused_value, incidents={"number": 5})

    def test_other_pause_values_do_not_pause(self):
        for value in ("", "false", "1", "yes"):
            with self.subTest(value=value):
                self.assertEqual(decide(paused_value=value)["action"], "sync")

    def test_open_pr_with_either_sync_label_blocks(self):
        for label in ("upstream-sync", "needs-adaptation"):
            with self.subTest(label=label):
                plan = decide(prs=[pr(12, labels=[label])])
                self.assertEqual(plan["action"], "skip")
                self.assertIn("#12", plan["reason"])

    def test_unlabelled_manual_sync_branch_blocks(self):
        plan = decide(prs=[pr(474, head="sync/umputun-v0.43.0")])
        self.assertEqual(plan["action"], "skip")
        self.assertIn("#474", plan["reason"])

    def test_unrelated_open_prs_do_not_block(self):
        prs = [pr(1, head="claude/x", labels=["bug"]), {**pr(2, head="upstream-sync-notes"), "labels": None}]
        self.assertEqual(decide(prs=prs)["action"], "sync")

    def test_fork_head_named_like_a_sync_branch_does_not_block(self):
        self.assertEqual(decide(prs=[pr(7, head="sync/umputun-v0.43.0", cross=True)])["action"], "sync")

    def test_labelled_fork_pr_still_blocks(self):
        self.assertEqual(decide(prs=[pr(7, head="x", labels=["needs-adaptation"], cross=True)])["action"], "skip")

    def test_pr_listing_without_the_cross_repository_field_fails_closed(self):
        with self.assertRaises(sync.SyncError):
            decide(prs=[{"number": 3, "headRefName": "sync/umputun-v0.43.0", "labels": []}])

    def test_malformed_pr_listing_fails_closed(self):
        with self.assertRaises(sync.SyncError):
            decide(prs={"number": 1})

    def test_existing_branch_without_an_open_pr_blocks_the_run(self):
        plan = decide(remote_branches={"sync/umputun-v0.43.0"})
        self.assertEqual(plan["action"], "blocked")
        self.assertIn("no open sync PR", plan["reason"])

    def test_branch_for_another_tag_does_not_block(self):
        self.assertEqual(decide(remote_branches={"sync/umputun-v0.42.3"})["action"], "sync")

    def test_up_to_date_main_skips(self):
        plan = decide(is_ancestor=contains("c" * 40))
        self.assertEqual((plan["action"], plan["tag"], plan["branch"]), ("skip", "", ""))


class TextTests(unittest.TestCase):
    def test_status_parsing_keeps_only_unmerged_entries(self):
        status = "UU a.swift\0M  b.swift\0AA c\0DU d\0UD e\0R  new.txt\0UU looks-unmerged\0?? f\0"
        self.assertEqual(sync.conflicts_from_status(status),
                         [("a.swift", "both modified"), ("c", "both added"), ("d", "deleted by us"),
                          ("e", "deleted by them")])

    def test_clean_pr_is_ready_and_labelled_upstream_sync(self):
        title, label, draft, body = text()
        self.assertEqual((title, label, draft), ("Sync umputun/agterm v0.43.0", "upstream-sync", False))
        self.assertIn("c" * 40, body)

    def test_every_body_passes_the_pr_fields_checker(self):
        for conflicts, github in (((), ()), ([("a", "both modified")], ()), ((), [".github/workflows/ci.yml"])):
            with self.subTest(conflicts=conflicts, github=github):
                body = text(conflicts, github, head="2" * 40)[3]
                self.assertEqual(pr_fields.problems(body, "2" * 40), [])
                self.assertEqual(pr_fields.values(body)["Task"], ["#476"])

    def test_every_body_names_both_base_pin_files_with_the_new_base(self):
        for conflicts in ((), [("a", "both modified")]):
            with self.subTest(conflicts=conflicts):
                body = text(conflicts)[3]
                self.assertIn("`.github/workflows/omarchy-ci.yml` and `.github/workflows/release-linux.yml`", body)
                self.assertIn(f"`v0.43.0` / `{'c' * 40}`", body)

    def test_github_changes_force_a_draft_listing_them(self):
        _, label, draft, body = text(github_changes=[".github/workflows/ci.yml"])
        self.assertEqual((label, draft), ("needs-adaptation", True))
        self.assertIn("## Changes under .github/ (1)", body)
        self.assertIn("- `.github/workflows/ci.yml`", body)
        self.assertNotIn("## Conflicts", body)

    def test_conflicted_pr_is_a_draft_listing_every_path(self):
        conflicts = [("a.swift", "both modified"), ("b", "deleted by them")]
        _, label, draft, body = text(conflicts)
        self.assertEqual((label, draft), ("needs-adaptation", True))
        self.assertIn("## Conflicts (2)", body)
        self.assertIn("- `a.swift` (both modified)", body)
        self.assertIn("- `b` (deleted by them)", body)

    def test_long_lists_are_truncated_below_the_body_limit(self):
        long = "agtermCore/Sources/agtermCore/" + "x" * 400
        conflicts = [(f"{long}{n:04}", "deleted by them") for n in range(1000)]
        github = [f".github/{long}{n:04}" for n in range(1000)]
        body = text(conflicts, github)[3]
        self.assertRegex(body, r"- and [0-9]+ more\n\n## Changes under")
        self.assertTrue(body.rstrip().splitlines()[-1].endswith(" more"))
        self.assertLess(len(body), 65536)

    def test_short_lists_are_complete(self):
        body = text([(f"f{n}", "both modified") for n in range(300)])[3]
        self.assertIn("- `f299` (both modified)", body)
        self.assertNotIn(" more", body)


class PauseCommandTests(unittest.TestCase):
    def run_pause(self, paused, incidents=None):
        with tempfile.TemporaryDirectory() as tmp:
            listing_path, output = Path(tmp) / "incidents.json", Path(tmp) / "out"
            listing = []
            if incidents is not None:
                listing_path.write_text(incidents, encoding="utf-8")
                listing = ["--incidents", str(listing_path)]
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = sync.main(["pause", "--paused", paused, *listing])
            return code, output.read_text(encoding="utf-8") if output.exists() else ""

    def test_the_variable_pauses_without_an_incident_listing(self):
        self.assertEqual(self.run_pause("true"), (0, "paused=true\nreason=SYNC_PAUSED is true\n"))

    def test_an_unpaused_variable_defers_to_the_incident_listing(self):
        for value in ("", "false"):
            with self.subTest(value=value):
                self.assertEqual(self.run_pause(value), (sync.UNDECIDED, ""))

    def test_unpaused(self):
        self.assertEqual(self.run_pause("", "[]"), (0, "paused=false\nreason=\n"))

    def test_variable_pauses(self):
        self.assertEqual(self.run_pause("true", "[]"), (0, "paused=true\nreason=SYNC_PAUSED is true\n"))

    def test_open_incident_pauses(self):
        code, output = self.run_pause("false", '[{"number": 482}]')
        self.assertEqual(code, 0)
        self.assertIn("paused=true\n", output)
        self.assertIn("#482", output)

    def test_unreadable_incident_listing_fails(self):
        self.assertEqual(self.run_pause("", "not json"), (2, ""))


class WorkflowOrderTests(unittest.TestCase):
    def position(self, text):
        self.assertEqual(WORKFLOW.count(text), 1, text)
        return WORKFLOW.index(text)

    def test_the_variable_then_incidents_decide_the_pause_before_any_umputun_fetch(self):
        variable = self.position('python3 scripts/upstream-sync.py pause --paused "$SYNC_PAUSED" || status=$?')
        self.assertLess(variable, self.position("gh issue list --state open --label release-incident"))
        self.assertLess(self.position("--incidents \"$RUNNER_TEMP/incidents.json\"\n"),
                        self.position("https://github.com/umputun/agterm.git"))
        self.assertEqual(WORKFLOW.count("if: steps.pause.outputs.paused == 'false'"), 3)

    def test_labels_exist_before_the_publish_script_runs(self):
        self.assertLess(self.position("name: Create the sync labels if missing"),
                        self.position("run: scripts/upstream-sync-publish.sh"))
        self.assertNotIn("labels[]=", WORKFLOW)


STUB = r"""#!/usr/bin/env bash
printf '%s\t%s\t%s\n' "$(basename "$0")" "${GH_TOKEN:-}|${GIT_CONFIG_VALUE_0:-}" "$*" >> "$STUB_LOG"
if [[ "$(basename "$0")" == git ]]; then
  ref=refs/heads/sync/umputun-v0.43.0
  [[ "$*" == *"$ref:$ref"* ]] || exit 0
  case "${STUB_PUSH:-new}" in
    new) printf '*\t%s:%s\t[new branch]\n' "$ref" "$ref" ;;
    same) printf '=\t%s:%s\t[up to date]\n' "$ref" "$ref" ;;
    *) printf '!\t%s:%s\t[rejected] (stale info)\n' "$ref" "$ref"; exit 1 ;;
  esac
  exit 0
fi
case "$1 $2" in
  "pr create") [[ "$STUB_CREATE" == ok ]] && { echo "https://github.com/o/r/pull/12"; exit 0; }; exit 1 ;;
  "pr list") [[ -z "${STUB_LIST_FAILS:-}" ]] || exit 1; echo "${STUB_FOUND:-}" ;;
  "pr close") [[ -z "${STUB_CLOSE_FAILS:-}" ]] || exit 1 ;;
  "api "*)
    if [[ " $* " == *" -f "* ]]; then
      echo x >> "$STUB_STATE.adds"
    elif [[ -n "${STUB_LABEL_AFTER:-}" && -f "$STUB_STATE.adds" ]] \
        && (( $(wc -l < "$STUB_STATE.adds") >= STUB_LABEL_AFTER )); then
      echo needs-adaptation
    fi ;;
esac
"""


class PublishScriptTests(unittest.TestCase):
    """The publish step against stubbed gh and git: what it calls, as whom, and how it recovers."""

    def run_publish(self, **stub):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            for tool in ("gh", "git"):
                (root / "bin" / tool).write_text(STUB, encoding="utf-8")
                (root / "bin" / tool).chmod(0o755)
            (root / "body.md").write_text("body", encoding="utf-8")
            env = {"PATH": f"{root / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(root / "log"),
                   "STUB_STATE": str(root / "state"), "GITHUB_OUTPUT": str(root / "out"),
                   "SYNC_TOKEN": "pat-secret", "GH_REPO": "o/r", "BRANCH": "sync/umputun-v0.43.0",
                   "HEAD_SHA": "2" * 40, "TITLE": "Sync umputun/agterm v0.43.0", "DRAFT": "true",
                   "LABEL": "needs-adaptation", "BODY_FILE": str(root / "body.md"), "LABEL_RETRY_DELAY": "0",
                   **stub}
            result = subprocess.run(["bash", str(PUBLISH)], env=env, capture_output=True, text=True)
            log = (root / "log").read_text(encoding="utf-8").splitlines() if (root / "log").exists() else []
            calls = [line.split("\t") for line in log]
            output = (root / "out").read_text(encoding="utf-8") if (root / "out").exists() else ""
            return result, calls, output

    def assert_all_as_pat(self, calls):
        for tool, identity, _ in calls:
            token, header = identity.split("|")
            self.assertEqual(token, "pat-secret", tool)
            self.assertIn(base64.b64encode(b"x-access-token:pat-secret").decode(), header)

    def args(self, calls, tool, prefix):
        return [args for name, _, args in calls if name == tool and args.startswith(prefix)]

    def test_success_pushes_create_only_and_labels_in_the_create_call(self):
        result, calls, output = self.run_publish(STUB_CREATE="ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_all_as_pat(calls)
        self.assertEqual(self.args(calls, "git", "push"), [
            "push --porcelain --force-with-lease=refs/heads/sync/umputun-v0.43.0: origin "
            "refs/heads/sync/umputun-v0.43.0:refs/heads/sync/umputun-v0.43.0"])
        create = self.args(calls, "gh", "pr create")
        self.assertEqual(len(create), 1)
        self.assertIn("--label needs-adaptation", create[0])
        self.assertIn("--draft", create[0])
        self.assertEqual(output, "number=12\n")
        self.assertNotIn("pat-secret", "".join(args for _, _, args in calls))

    def test_a_pr_opened_without_its_label_is_labelled_by_retry(self):
        result, calls, output = self.run_publish(STUB_CREATE="label-fails", STUB_FOUND="12", STUB_LABEL_AFTER="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_all_as_pat(calls)
        self.assertEqual(len(self.args(calls, "gh", "api repos/o/r/issues/12/labels -f labels[]=needs-adaptation")), 1)
        self.assertEqual(self.args(calls, "gh", "pr close"), [])
        self.assertEqual(len(self.args(calls, "git", "push")), 1)
        self.assertEqual(output, "number=12\n")

    def test_a_pr_that_cannot_be_labelled_is_closed_before_its_branch_is_deleted(self):
        result, calls, output = self.run_publish(STUB_CREATE="label-fails", STUB_FOUND="12")
        self.assertEqual(result.returncode, 1)
        self.assert_all_as_pat(calls)
        self.assertEqual(len(self.args(calls, "gh", "api repos/o/r/issues/12/labels -f")), 3)
        names = [f"{name} {args.split()[0]} {args.split()[1]}" for name, _, args in calls]
        self.assertLess(names.index("gh pr close"), len(names) - 1)
        self.assertEqual(calls[-1][2], f"push --force-with-lease=refs/heads/sync/umputun-v0.43.0:{'2' * 40} "
                                       "origin :refs/heads/sync/umputun-v0.43.0")
        self.assertEqual(output, "")

    def test_a_failed_create_with_no_pr_deletes_the_branch(self):
        result, calls, _ = self.run_publish(STUB_CREATE="fails", STUB_FOUND="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.args(calls, "gh", "pr close"), [])
        self.assertIn(":refs/heads/sync/umputun-v0.43.0", calls[-1][2])

    def test_a_failed_close_or_lookup_still_deletes_the_branch_and_fails(self):
        for stub, error in (({"STUB_FOUND": "12", "STUB_CLOSE_FAILS": "1"}, "::error::could not close #12"),
                            ({"STUB_LIST_FAILS": "1"}, "::error::no PR from sync/umputun-v0.43.0 was found")):
            with self.subTest(stub=stub):
                result, calls, output = self.run_publish(STUB_CREATE="label-fails", **stub)
                self.assertEqual(result.returncode, 1)
                self.assertIn(error, result.stderr)
                self.assertIn(":refs/heads/sync/umputun-v0.43.0", calls[-1][2])
                self.assertEqual(output, "")

    def test_an_existing_branch_stops_before_any_pr_and_is_never_deleted(self):
        for push in ("stale", "same"):
            with self.subTest(push=push):
                result, calls, _ = self.run_publish(STUB_CREATE="ok", STUB_PUSH=push)
                self.assertEqual(result.returncode, 1)
                self.assertIn("::error::", result.stderr)
                self.assertEqual([name for name, _, _ in calls], ["git"])

    def test_missing_inputs_fail_before_any_call(self):
        result, calls, _ = self.run_publish(STUB_CREATE="ok", SYNC_TOKEN="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])


class GitRepoTests(unittest.TestCase):
    """Real git: tag peeling, the ancestor check, and both merge outcomes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.commit("shared.txt", "base\n", "base")
        self.base_tag = self.head()
        self.git("checkout", "-q", "-b", "upstream")
        self.commit("shared.txt", "upstream\n", "upstream change")
        self.commit("upstream-only.txt", "new\n", "upstream file")
        self.upstream = self.head()
        self.git("checkout", "-q", "main")
        self.commit("fork-only.txt", "fork\n", "fork file")
        self.git("tag", "-a", "-m", "release", "annotated", self.upstream)
        self.git("update-ref", "refs/upstream-tags/v0.2.0", self.git("rev-parse", "refs/tags/annotated"))
        self.git("tag", "-d", "annotated")
        self.git("update-ref", "refs/upstream-tags/v0.1.0", self.base_tag)
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "tag.gpgSign=false",
                               "-c", "commit.gpgSign=false", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, name, text, message):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def test_fetched_tags_are_peeled_to_commits(self):
        self.assertEqual(sync.fetched_tags(), {"v0.1.0": self.base_tag, "v0.2.0": self.upstream})

    def test_a_tag_of_a_tag_is_peeled_to_its_commit(self):
        self.git("tag", "-a", "-m", "inner", "inner", self.upstream)
        self.git("tag", "-a", "-m", "outer", "outer", "inner")
        self.git("update-ref", "refs/upstream-tags/v0.3.0", self.git("rev-parse", "refs/tags/outer"))
        self.assertEqual(sync.fetched_tags()["v0.3.0"], self.upstream)

    def test_ancestor_check_reads_the_real_history(self):
        is_ancestor = sync.ancestor_check(self.head())
        self.assertTrue(is_ancestor(self.base_tag))
        self.assertFalse(is_ancestor(self.upstream))
        with self.assertRaises(sync.SyncError):
            is_ancestor("1" * 40)

    def test_plan_command_selects_the_unmerged_release(self):
        prs, branches, incidents = self.root / "prs.json", self.root / "branches.txt", self.root / "incidents.json"
        incidents.write_text("[]", encoding="utf-8")
        prs.write_text("[]", encoding="utf-8")
        branches.write_text(f"{'0' * 40}\trefs/heads/sync/umputun-v0.1.0\n", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "out")}), contextlib.redirect_stdout(out):
            self.assertEqual(sync.main(["plan", "--base", "main", "--open-prs", str(prs),
                                        "--remote-branches", str(branches), "--incidents", str(incidents)]), 0)
        self.assertEqual(json.loads(out.getvalue())["tag"], "v0.2.0")
        self.assertIn("action=sync\n", (self.root / "out").read_text(encoding="utf-8"))

    def test_plan_command_honours_an_open_release_incident(self):
        prs, branches, incidents = self.root / "prs.json", self.root / "branches.txt", self.root / "incidents.json"
        prs.write_text("[]", encoding="utf-8")
        branches.write_text("", encoding="utf-8")
        incidents.write_text('[{"number": 482}]', encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "out")}), contextlib.redirect_stdout(out):
            self.assertEqual(sync.main(["plan", "--base", "main", "--open-prs", str(prs),
                                        "--remote-branches", str(branches), "--incidents", str(incidents)]), 0)
        plan = json.loads(out.getvalue())
        self.assertEqual((plan["action"], plan["tag"]), ("skip", ""))
        self.assertIn("#482", plan["reason"])

    def test_plan_command_requires_the_incident_listing(self):
        prs, branches = self.root / "prs.json", self.root / "branches.txt"
        prs.write_text("[]", encoding="utf-8")
        branches.write_text("", encoding="utf-8")
        for extra in ([], ["--incidents", str(self.root / "missing.json")]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = sync.main(["plan", "--base", "main", "--open-prs", str(prs),
                                      "--remote-branches", str(branches), *extra])
                except SystemExit as exit_:
                    code = exit_.code
                self.assertEqual(code, 2)

    def test_plan_command_fails_closed_on_unreadable_pr_listing(self):
        prs, branches, incidents = self.root / "prs.json", self.root / "branches.txt", self.root / "incidents.json"
        incidents.write_text("[]", encoding="utf-8")
        prs.write_text("not json", encoding="utf-8")
        branches.write_text("", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sync.main(["plan", "--base", "main", "--open-prs", str(prs),
                                        "--remote-branches", str(branches), "--incidents", str(incidents)]), 2)

    def test_clean_merge_is_a_two_parent_commit(self):
        self.git("checkout", "-q", "-b", "nonconflicting", self.base_tag)
        self.commit("other.txt", "x\n", "upstream file only")
        clean = self.head()
        self.git("checkout", "-q", "main")
        conflicts, github = sync.build(self.root / "wt", self.head(), "sync/umputun-v0.3.0", "v0.3.0", clean)
        self.assertEqual((conflicts, github), ([], []))
        parents = self.git("rev-list", "--parents", "-n", "1", "sync/umputun-v0.3.0").split()[1:]
        self.assertEqual(parents, [self.head(), clean])

    def test_merge_advances_downstream_pins_without_editing_upstream_ci(self):
        pins = f'env:\n  UPSTREAM_BASE_TAG: "v0.1.0"\n  UPSTREAM_BASE_COMMIT: "{self.base_tag}"\n'
        for name in (sync.CI_WORKFLOW, sync.RELEASE_WORKFLOW, ".github/workflows/ci.yml"):
            self.commit(name, pins, f"initial pins: {name}")
        branch = "sync/umputun-v0.2.0"
        conflicts, github = sync.build(self.root / "wt", self.head(), branch, "v0.2.0", self.upstream)

        self.assertEqual((conflicts, github), ([], []))
        for name in (sync.CI_WORKFLOW, sync.RELEASE_WORKFLOW):
            content = self.git("show", f"{branch}:{name}")
            self.assertIn('UPSTREAM_BASE_TAG: "v0.2.0"', content)
            self.assertIn(f'UPSTREAM_BASE_COMMIT: "{self.upstream}"', content)
        self.assertEqual(self.git("show", f"{branch}:.github/workflows/ci.yml"), pins.strip())

    def test_conflicts_are_committed_with_markers_and_both_parents(self):
        self.commit("shared.txt", "fork\n", "fork change")
        main = self.head()
        conflicts, _ = sync.build(self.root / "wt", main, "sync/umputun-v0.2.0", "v0.2.0", self.upstream)
        self.assertEqual(conflicts, [("shared.txt", "both modified")])
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", "sync/umputun-v0.2.0").split()[1:],
                         [main, self.upstream])
        self.assertIn("<<<<<<<", self.git("show", "sync/umputun-v0.2.0:shared.txt"))
        self.assertEqual(self.git("show", "sync/umputun-v0.2.0:upstream-only.txt"), "new")
        self.assertEqual(self.git("-C", str(self.root / "wt"), "status", "--porcelain"), "")
        self.assertEqual(self.head(), main)

    def test_upstream_github_changes_are_reported(self):
        self.git("checkout", "-q", "-b", "workflow", self.base_tag)
        (self.repo / ".github/workflows").mkdir(parents=True)
        self.commit(".github/workflows/upstream.yml", "on: push\n", "upstream workflow")
        upstream = self.head()
        self.git("checkout", "-q", "main")
        _, github = sync.build(self.root / "wt", self.head(), "sync/umputun-v0.4.0", "v0.4.0", upstream)
        self.assertEqual(github, [".github/workflows/upstream.yml"])

    def test_a_merge_that_records_no_upstream_parent_fails(self):
        with self.assertRaisesRegex(sync.SyncError, "parents"):
            sync.build(self.root / "wt", self.head(), "sync/umputun-v0.1.0", "v0.1.0", self.base_tag)

    def test_merge_command_writes_a_body_the_checker_accepts(self):
        body, out = self.root / "body.md", io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "out")}), contextlib.redirect_stdout(out):
            self.assertEqual(sync.main(["merge", "--base", "main", "--branch", "sync/umputun-v0.2.0", "--tag", "v0.2.0",
                                        "--commit", self.upstream, "--worktree", str(self.root / "wt"),
                                        "--body", str(body), "--run-url", RUN]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["head"], self.git("rev-parse", "sync/umputun-v0.2.0"))
        self.assertEqual(pr_fields.problems(body.read_text(encoding="utf-8"), result["head"]), [])


if __name__ == "__main__":
    unittest.main()
