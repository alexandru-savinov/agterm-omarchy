#!/usr/bin/env python3
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("upstream_sync", Path(__file__).resolve().parents[1] / "upstream-sync.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
LIVENESS_SPEC = importlib.util.spec_from_file_location(
    "upstream_sync_liveness", Path(__file__).resolve().parents[1] / "upstream-sync-liveness.py")
liveness = importlib.util.module_from_spec(LIVENESS_SPEC)
LIVENESS_SPEC.loader.exec_module(liveness)
FIELDS_SPEC = importlib.util.spec_from_file_location("pr_fields", Path(__file__).resolve().parents[1] / "github/pr_fields.py")
pr_fields = importlib.util.module_from_spec(FIELDS_SPEC)
FIELDS_SPEC.loader.exec_module(pr_fields)
RUN = "https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/1"
WORKFLOW = (Path(__file__).resolve().parents[2] / ".github/workflows/upstream-sync.yml").read_text(encoding="utf-8")
PUBLISH = Path(__file__).resolve().parents[1] / "upstream-sync-publish.sh"
REHEARSAL = PUBLISH.with_name("upstream-sync-rehearsal.sh")
REHEARSAL_REPO = "alexandru-savinov/agterm-omarchy"
REHEARSAL_BRANCH = "rehearsal/linux-port-314-2"
BLOCKED = Path(__file__).resolve().parents[1] / "upstream-sync-blocked.sh"
CI_WORKFLOW = (Path(__file__).resolve().parents[2] / ".github/workflows/omarchy-ci.yml").read_text(encoding="utf-8")

TAGS = {"v0.41.1": "a" * 40, "v0.42.3": "b" * 40, "v0.43.0": "c" * 40, "v0.9.9": "d" * 40}
PORT = "e" * 40  # the linux-port head
PORT_BRANCH = "sync/linux-port-eeeeeeee"
NOW = 1_800_000_000
LAG = sync.LINUX_PORT_LAG_DAYS * sync.DAY
BEHIND = ("a" * 40, "b" * 40, "d" * 40)  # a linux-port that carries every release but v0.43.0
BEHIND_TAGS = ("v0.41.1", "v0.42.3", "v0.9.9")


def contains(*commits):
    return lambda commit: commit in commits


def pr(number, head="x", labels=(), cross=False):
    return {"number": number, "headRefName": head, "labels": [{"name": name} for name in labels],
            "isCrossRepository": cross}


def text(conflicts=(), github_changes=(), head="2" * 40, source="umputun", commit="c" * 40,
         pin=("v0.43.0", "c" * 40)):
    return sync.pr_text(source=source, commit=commit, release=("v0.43.0", "c" * 40), pin=pin, base_sha="1" * 40,
                        head_sha=head, run_url=RUN, conflicts=list(conflicts), github_changes=list(github_changes))


def selection(*, main=(), port=TAGS.values(), ages=None, tags=TAGS, **overrides):
    """Arguments of `select`: main and linux-port by the commits they contain, tags 30 days old unless aged."""
    seconds = {tag: 30 * sync.DAY for tag in tags} | (ages or {})
    return {"tags": tags, "tag_times": {tag: NOW - age for tag, age in seconds.items()}, "linux_port": PORT,
            "in_main": contains(*main), "in_linux_port": contains(*port), "now": NOW, "override": "", **overrides}


def select(**scenario):
    chosen = sync.select(**selection(**scenario))
    return chosen["source"], chosen["tag"], chosen["commit"]


def decide(*, paused_value="", incidents=None, prs=None, remote_branches=(), **scenario):
    return sync.decide(paused_value=paused_value, incidents=[] if incidents is None else incidents,
                       prs=[] if prs is None else prs, remote_branches=set(remote_branches), **selection(**scenario))


NOTHING = ("", "", "")
DIRECT = ("umputun", "v0.43.0", "c" * 40)


class SourceSelectionTests(unittest.TestCase):
    def test_linux_port_is_selected_when_main_lacks_its_head(self):
        self.assertEqual(select(), ("linux-port", "", PORT))

    def test_linux_port_wins_over_an_overdue_direct_sync(self):
        self.assertEqual(select(port=BEHIND, ages={"v0.43.0": 30 * sync.DAY}), ("linux-port", "", PORT))

    def test_nothing_to_do_when_main_has_linux_port_and_linux_port_has_the_newest_release(self):
        chosen = sync.select(**selection(main=[PORT]))
        self.assertEqual((chosen["source"], chosen["commit"]), ("", ""))
        self.assertIn("carries the newest release v0.43.0", chosen["reason"])

    def test_the_lag_is_seven_days(self):
        self.assertEqual(sync.LINUX_PORT_LAG_DAYS, 7)

    def test_direct_sync_waits_while_linux_port_lags_within_the_window(self):
        for age in (0, sync.DAY, LAG):
            with self.subTest(age=age):
                chosen = sync.select(**selection(main=[PORT], port=BEHIND, ages={"v0.43.0": age}))
                self.assertEqual(chosen["source"], "")
                self.assertIn("has lacked v0.43.0", chosen["reason"])
                self.assertIn("waits for more than 7", chosen["reason"])

    def test_direct_sync_is_the_fallback_once_linux_port_lags_past_the_window(self):
        for age in (LAG + 1, 30 * sync.DAY):
            with self.subTest(age=age):
                self.assertEqual(select(main=[PORT], port=BEHIND, ages={"v0.43.0": age}), DIRECT)

    def test_the_window_follows_the_named_constant(self):
        scenario = {"main": [PORT], "port": BEHIND, "ages": {"v0.43.0": 4 * sync.DAY}}
        self.assertEqual(select(**scenario), NOTHING)
        with mock.patch.object(sync, "LINUX_PORT_LAG_DAYS", 3):
            self.assertEqual(select(**scenario), DIRECT)

    def test_each_release_has_its_own_lag_so_a_fresh_one_neither_syncs_nor_postpones_an_overdue_one(self):
        ages = {"v0.42.3": LAG + 1, "v0.43.0": sync.DAY}
        chosen = sync.select(**selection(main=[PORT], port=["a" * 40, "d" * 40], ages=ages))
        self.assertEqual((chosen["source"], chosen["tag"], chosen["commit"]), ("umputun", "v0.42.3", "b" * 40))
        self.assertIn("has lacked v0.42.3 for 7.0 days, more than 7", chosen["reason"])

    def test_the_newest_overdue_release_syncs(self):
        self.assertEqual(select(main=[PORT], port=["a" * 40, "d" * 40]), DIRECT)

    def test_a_wait_names_the_newest_lacking_release(self):
        ages = {"v0.42.3": 2 * sync.DAY, "v0.43.0": sync.DAY}
        chosen = sync.select(**selection(main=[PORT], port=["a" * 40, "d" * 40], ages=ages))
        self.assertEqual(chosen["source"], "")
        self.assertIn("has lacked v0.43.0 for 1.0 days; the direct sync waits", chosen["reason"])

    def test_an_overdue_release_below_one_main_holds_is_never_synced_backwards(self):
        for tags, port in ((TAGS, ["a" * 40, "d" * 40]), ({**TAGS, "v0.42.4": "f" * 40}, BEHIND)):
            with self.subTest(tags=sorted(tags)):
                ages = {"v0.43.0": sync.DAY}
                chosen = sync.select(**selection(main=[PORT, "c" * 40], port=port, tags=tags, ages=ages))
                self.assertEqual(chosen["source"], "")
                self.assertEqual(chosen["reason"], "main already contains v0.43.0")

    def test_a_future_dated_tag_waits(self):
        self.assertEqual(select(main=[PORT], port=BEHIND, ages={"v0.43.0": -sync.DAY}), NOTHING)

    def test_nothing_to_do_when_main_already_has_the_lagging_release(self):
        chosen = sync.select(**selection(main=[PORT, "c" * 40], port=BEHIND))
        self.assertEqual(chosen["source"], "")
        self.assertEqual(chosen["reason"], "main already contains v0.43.0")

    def test_older_non_ancestor_release_never_starts_the_lag_or_syncs_backwards(self):
        tags = {**TAGS, "v0.42.4": "f" * 40}
        self.assertEqual(select(main=[PORT], port=TAGS.values(), tags=tags), NOTHING)
        self.assertEqual(select(main=[PORT], port=["c" * 40], tags=tags), NOTHING)

    def test_versions_sort_numerically_not_lexically(self):
        tags = {"v0.9.0": "1" * 40, "v0.10.0": "2" * 40, "v0.10.10": "3" * 40, "v0.10.9": "4" * 40}
        self.assertEqual(select(main=[PORT], port=["1" * 40], tags=tags)[1], "v0.10.10")

    def test_non_release_tags_are_ignored(self):
        tags = {**TAGS, "v0.44.0-rc1": "f" * 40, "v1.0": "7" * 40, "v01.0.0": "9" * 40, "omarchy-v0.45.0.1": "8" * 40}
        self.assertEqual(select(main=[PORT], port=TAGS.values(), tags=tags), NOTHING)
        self.assertEqual(select(main=[PORT], port=BEHIND, tags=tags), DIRECT)

    def test_dry_run_override_previews_any_release_main_lacks(self):
        for scenario in ({"port": ["a" * 40], "ages": {"v0.42.3": 0}}, {"main": [PORT]}, {"main": [PORT, "c" * 40]}):
            with self.subTest(scenario=scenario):
                chosen = select(override="v0.42.3", dry_run=True, **scenario)
                self.assertEqual(chosen, ("umputun", "v0.42.3", "b" * 40))

    def test_live_override_waits_for_linux_port_to_merge_first(self):
        for dry_run in ({}, {"dry_run": False}):
            with self.subTest(dry_run=dry_run):
                chosen = sync.select(**selection(port=BEHIND, override="v0.43.0", **dry_run))
                self.assertEqual((chosen["source"], chosen["commit"]), ("", ""))
                self.assertEqual(chosen["reason"],
                                 "linux-port eeeeeeee is not an ancestor of main and syncs before v0.43.0")

    def test_live_override_waits_out_the_lag(self):
        scenario = {"main": [PORT], "port": BEHIND, "override": "v0.43.0"}
        chosen = sync.select(**selection(ages={"v0.43.0": LAG}, **scenario))
        self.assertEqual(chosen["source"], "")
        self.assertIn("has lacked v0.43.0 for 7.0 days; the direct sync waits", chosen["reason"])
        self.assertEqual(select(ages={"v0.43.0": LAG + 1}, **scenario), DIRECT)

    def test_live_override_never_takes_a_release_linux_port_or_main_already_passed(self):
        tags = {**TAGS, "v0.42.4": "f" * 40}
        for scenario in ({"port": TAGS.values()}, {"main": [PORT, "c" * 40], "port": BEHIND}):
            with self.subTest(scenario=scenario):
                chosen = sync.select(**selection(tags=tags, override="v0.42.4", **{"main": [PORT], **scenario}))
                self.assertEqual(chosen["source"], "")
                self.assertIn("v0.42.4 is not above v0.43.0", chosen["reason"])

    def test_live_override_narrows_the_direct_sync_to_an_older_overdue_release(self):
        self.assertEqual(select(main=[PORT], port=["a" * 40, "d" * 40], override="v0.42.3"),
                         ("umputun", "v0.42.3", "b" * 40))

    def test_override_already_in_main_is_nothing_to_do(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                self.assertEqual(select(main=["b" * 40], override="v0.42.3", dry_run=dry_run), NOTHING)

    def test_override_must_be_a_fetched_release_tag(self):
        for bad in ("v0.44.0", "main", "v0.43.0-rc1", "refs/tags/v0.43.0"):
            with self.subTest(bad=bad), self.assertRaises(sync.SyncError):
                select(override=bad)

    def test_no_release_tags_fails_instead_of_skipping(self):
        with self.assertRaises(sync.SyncError):
            select(tags={"latest": "a" * 40})

    def test_a_linux_port_without_any_release_fails(self):
        with self.assertRaisesRegex(sync.SyncError, "contains no umputun/agterm release"):
            select(main=[PORT], port=[])

    def test_a_lacking_release_without_a_lag_clock_fails(self):
        scenario = selection(main=[PORT], port=BEHIND)
        del scenario["tag_times"]["v0.43.0"]
        with self.assertRaisesRegex(sync.SyncError, "no lag clock for v0.43.0"):
            sync.select(**scenario)

    def test_newest_contained_release_is_by_version(self):
        self.assertEqual(sync.newest_contained(TAGS, contains("a" * 40, "b" * 40, "d" * 40)), "v0.42.3")
        self.assertIsNone(sync.newest_contained({**TAGS, "latest": "f" * 40}, contains("f" * 40)))


def release(tag, published="2026-11-17T19:07:29Z", draft=False, **extra):
    return {"tag_name": tag, "draft": draft, "published_at": published, **extra}


PUBLISHED = 1794942449  # 2026-11-17T19:07:29Z


class LagClockTests(unittest.TestCase):
    def clocks(self, releases, first_seen=None, tags=TAGS):
        return sync.lag_clocks(tags, releases, first_seen or {}, NOW)

    def test_a_published_release_sets_the_clock(self):
        clocks, kept = self.clocks([release(tag) for tag in TAGS])
        self.assertEqual((clocks, kept), (dict.fromkeys(TAGS, PUBLISHED), {}))

    def test_the_release_creation_time_is_never_the_clock(self):
        created = release("v0.43.0", created_at="2026-10-01T00:00:00Z")
        self.assertEqual(self.clocks([created])[0]["v0.43.0"], PUBLISHED)

    def test_a_tag_without_a_published_release_starts_when_first_seen(self):
        unpublished = ([], [release("v0.43.0", draft=True)], [release("v0.43.0", published=None)],
                       [{"tag_name": "v0.43.0", "published_at": "2026-11-17T19:07:29Z"}], [release("v0.42.3")])
        for releases in unpublished:
            with self.subTest(releases=releases):
                clocks, kept = self.clocks(releases)
                self.assertEqual((clocks["v0.43.0"], kept["v0.43.0"]), (NOW, {"commit": "c" * 40, "seen": NOW}))

    def test_a_recorded_first_sighting_is_kept_and_a_lost_one_restarts_now(self):
        seen = {"commit": "c" * 40, "seen": NOW - 3 * sync.DAY}
        clocks, kept = self.clocks([], {"v0.43.0": seen, "v0.1.0": {"commit": "9" * 40, "seen": 5}})
        self.assertEqual((clocks["v0.43.0"], kept["v0.43.0"]), (NOW - 3 * sync.DAY, seen))
        self.assertEqual(clocks["v0.42.3"], NOW)
        self.assertEqual(sorted(kept), sorted(TAGS))

    def test_a_tag_moved_to_another_commit_starts_a_fresh_clock(self):
        seen = {"v0.43.0": {"commit": "c" * 40, "seen": NOW - 30 * sync.DAY}}
        moved = {**TAGS, "v0.43.0": "f" * 40}
        clocks, kept = self.clocks([], seen, tags=moved)
        self.assertEqual((clocks["v0.43.0"], kept["v0.43.0"]), (NOW, {"commit": "f" * 40, "seen": NOW}))
        back, kept_back = sync.lag_clocks(TAGS, [], kept, NOW + sync.DAY)
        self.assertEqual((back["v0.43.0"], kept_back["v0.43.0"]["commit"]), (NOW + sync.DAY, "c" * 40))

    def test_a_moved_tag_is_not_overdue_on_its_old_clock(self):
        seen = {"v0.43.0": {"commit": "c" * 40, "seen": NOW - 30 * sync.DAY}}
        for commit, expected in (("c" * 40, DIRECT), ("f" * 40, NOTHING)):
            with self.subTest(commit=commit):
                tags = {**TAGS, "v0.43.0": commit}
                clocks, _ = sync.lag_clocks(tags, [release(tag) for tag in BEHIND_TAGS], seen, NOW)
                chosen = sync.select(**{**selection(main=[PORT], port=BEHIND, tags=tags), "tag_times": clocks})
                self.assertEqual((chosen["source"], chosen["tag"]), expected[:2])

    def test_a_release_replaces_the_first_sighting_and_drops_it_from_the_record(self):
        seen = {"v0.43.0": {"commit": "c" * 40, "seen": NOW - 30 * sync.DAY}}
        clocks, kept = self.clocks([release("v0.43.0")], seen)
        self.assertEqual(clocks["v0.43.0"], PUBLISHED)
        self.assertNotIn("v0.43.0", kept)

    def test_non_release_tags_get_no_clock(self):
        clocks, kept = self.clocks([], tags={"v0.44.0-rc1": "f" * 40, "latest": "1" * 40})
        self.assertEqual((clocks, kept), ({}, {}))

    def test_offsets_are_honoured(self):
        self.assertEqual(self.clocks([release("v0.43.0", "2026-11-17T21:07:29+02:00")])[0]["v0.43.0"], PUBLISHED)

    def test_malformed_inputs_fail_closed(self):
        bad_releases = ({"message": "Not Found"}, ["v0.43.0"], [release("v0.43.0", "yesterday")],
                        [release("v0.43.0", "2026-11-17T19:07:29")])
        for releases in bad_releases:
            with self.subTest(releases=releases), self.assertRaises(sync.SyncError):
                self.clocks(releases)
        entry = {"commit": "c" * 40, "seen": NOW}
        for first_seen in (["v0.43.0"], {"v0.43.0": NOW}, {"v0.43.0": {**entry, "seen": "1"}},
                           {"v0.43.0": {**entry, "seen": 1.5}}, {"v0.43.0": {**entry, "seen": True}},
                           {"v0.43.0": {"seen": NOW}}, {"v0.43.0": {**entry, "commit": None}}):
            with self.subTest(first_seen=first_seen), self.assertRaises(sync.SyncError):
                sync.lag_clocks(TAGS, [], first_seen, NOW)


def http(status, body, reason="OK"):
    """`gh api --include` output: an LF status line, CRLF headers, a blank line, the body."""
    return f"HTTP/2.0 {status} {reason}\nContent-Type: application/json\r\nX-Github-Request-Id: 1\r\n\r\n{body}"


class SettingsGuardTests(unittest.TestCase):
    PERMISSIONS = {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False}
    SECRETS = {"total_count": 0, "secrets": []}
    DENIED = {"message": "Resource not accessible by personal access token", "status": "403"}

    def check(self, permissions=(200, PERMISSIONS), secrets=(200, SECRETS)):
        return sync.settings_check(permissions, secrets)

    def test_response_parsing_reads_the_status_and_the_json_body(self):
        self.assertEqual(sync.http_response(http(200, json.dumps(self.PERMISSIONS))), (200, self.PERMISSIONS))
        self.assertEqual(sync.http_response(http(403, json.dumps(self.DENIED), "Forbidden")), (403, self.DENIED))
        self.assertEqual(sync.http_response("HTTP/1.1 502 Bad Gateway\r\n\r\n<html>"), (502, None))
        self.assertEqual(sync.http_response(http(200, "")), (200, None))
        for nothing in ("", "\n", '{"default_workflow_permissions": "read"}', "gh: connection refused",
                        "gh: error\nHTTP/2.0 403 Forbidden\r\n\r\n{}", " HTTP/2.0 403 Forbidden\n\n{}"):
            with self.subTest(nothing=nothing):
                self.assertIsNone(sync.http_response(nothing)[0])

    def test_read_only_token_and_no_repository_secret_is_safe(self):
        self.assertEqual(self.check(), ([], []))

    def test_a_writable_default_token_is_unsafe(self):
        problems, unverified = self.check(permissions=(200, {"default_workflow_permissions": "write"}))
        self.assertEqual((problems, unverified), (["default workflow permissions are 'write', not 'read'"], []))

    def test_any_repository_secret_is_unsafe(self):
        listing = {"total_count": 2, "secrets": [{"name": "A"}, {"name": "B"}]}
        self.assertEqual(self.check(secrets=(200, listing)), (["repository secrets exist (2): A, B"], []))
        for listing in ({"total_count": 3, "secrets": []}, {"total_count": 0, "secrets": [{"name": "A"}]}):
            with self.subTest(listing=listing):
                self.assertEqual(len(self.check(secrets=(200, listing))[0]), 1)

    def test_a_setting_the_token_may_not_read_is_unverified_not_unsafe(self):
        for status in (403, 404):
            with self.subTest(status=status):
                denied = (status, self.DENIED)
                self.assertEqual(self.check(permissions=denied), ([], ["default workflow permissions"]))
                self.assertEqual(self.check(secrets=denied), ([], ["repository secrets"]))
                self.assertEqual(self.check(denied, denied),
                                 ([], ["default workflow permissions", "repository secrets"]))

    def test_an_unreadable_setting_never_hides_a_readable_unsafe_one(self):
        denied, listed = (403, self.DENIED), (200, {"total_count": 1, "secrets": [{"name": "A"}]})
        self.assertEqual(self.check(permissions=denied, secrets=listed),
                         (["repository secrets exist (1): A"], ["default workflow permissions"]))
        problems, unverified = self.check(permissions=(200, {"default_workflow_permissions": "write"}), secrets=denied)
        self.assertEqual((len(problems), unverified), (1, ["repository secrets"]))

    def test_any_other_failure_to_read_a_setting_is_unsafe(self):
        for status in (None, 401, 429, 500, 502, 301):
            with self.subTest(status=status):
                self.assertEqual(len(self.check(permissions=(status, self.DENIED))[0]), 1)
                self.assertEqual(len(self.check(secrets=(status, None))[0]), 1)
        self.assertEqual(self.check(secrets=(None, None))[0],
                         ["repository secrets could not be read (no HTTP response)"])
        self.assertEqual(self.check(secrets=(500, None))[0], ["repository secrets could not be read (HTTP 500)"])

    def test_a_malformed_readable_answer_is_unsafe(self):
        for body in (None, [], self.DENIED, {"default_workflow_permissions": None}):
            with self.subTest(body=body):
                self.assertEqual(len(self.check(permissions=(200, body))[0]), 1)
        for body in (None, [], self.DENIED, {"secrets": []}, {"total_count": "0", "secrets": []}, {"total_count": 0}):
            with self.subTest(body=body):
                self.assertEqual(len(self.check(secrets=(200, body))[0]), 1)

    def run_guard(self, permissions, secrets):
        """(exit status, stdout, stderr) of the `guard` command on two `gh api --include` outputs."""
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "permissions.http", Path(tmp) / "secrets.http"]
            for path, content in zip(paths, (permissions, secrets), strict=True):
                path.write_text(content, encoding="utf-8", newline="")
            out, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
                code = sync.main(["guard", "--workflow-permissions", str(paths[0]), "--secrets", str(paths[1])])
            return code, out.getvalue(), error.getvalue()

    def test_guard_command_passes_safe_settings_without_a_warning(self):
        code, out, error = self.run_guard(http(200, json.dumps(self.PERMISSIONS)), http(200, json.dumps(self.SECRETS)))
        self.assertEqual((code, error), (0, ""))
        self.assertNotIn("::warning::", out)

    def test_guard_command_warns_and_continues_when_the_token_may_not_read(self):
        denied = json.dumps(self.DENIED)
        for status, reason in ((403, "Forbidden"), (404, "Not Found")):
            with self.subTest(status=status):
                code, out, error = self.run_guard(http(status, denied, reason), http(status, denied, reason))
                self.assertEqual((code, error), (0, ""))
                self.assertRegex(out, r"(?m)^::warning::upstream-sync could not verify default workflow permissions "
                                      r"and repository secrets: ")
        code, out, _ = self.run_guard(http(200, json.dumps(self.PERMISSIONS)), http(403, denied, "Forbidden"))
        self.assertEqual(code, 0)
        self.assertIn("::warning::upstream-sync could not verify repository secrets: ", out)

    def test_guard_command_fails_on_what_it_can_read_even_beside_an_unreadable_setting(self):
        code, out, error = self.run_guard(http(200, '{"default_workflow_permissions": "write"}'),
                                          http(403, json.dumps(self.DENIED), "Forbidden"))
        self.assertEqual(code, 2)
        self.assertIn("::warning::upstream-sync could not verify repository secrets: ", out)
        self.assertIn("a sync push is unsafe: default workflow permissions are 'write', not 'read'", error)
        code, _, error = self.run_guard(http(200, json.dumps(self.PERMISSIONS)),
                                        http(200, '{"total_count": 1, "secrets": [{"name": "A"}]}'))
        self.assertEqual(code, 2)
        self.assertIn("repository secrets exist (1): A", error)

    def test_guard_command_fails_on_any_other_error_or_no_answer(self):
        safe = http(200, json.dumps(self.PERMISSIONS))
        for answer in ("", http(500, '{"message": "Server Error"}', "Internal Server Error"), http(200, "not json"),
                       http(401, '{"message": "Bad credentials"}', "Unauthorized"), json.dumps(self.SECRETS)):
            with self.subTest(answer=answer):
                code, out, _ = self.run_guard(safe, answer)
                self.assertEqual(code, 2)
                self.assertNotIn("::warning::", out)


class GateTests(unittest.TestCase):
    FALLBACK = {"main": [PORT], "port": BEHIND}

    def test_clean_state_syncs_linux_port(self):
        plan = decide()
        self.assertEqual((plan["action"], plan["source"], plan["tag"], plan["commit"], plan["branch"]),
                         ("sync", "linux-port", "", PORT, PORT_BRANCH))

    def test_fallback_syncs_the_release_on_its_own_branch(self):
        plan = decide(**self.FALLBACK)
        self.assertEqual((plan["action"], plan["source"], plan["tag"], plan["commit"], plan["branch"]),
                         ("sync", "umputun", "v0.43.0", "c" * 40, "sync/umputun-v0.43.0"))

    def test_pause_wins_over_every_other_gate(self):
        for value in ("true", "TRUE", " true\n"):
            with self.subTest(value=value):
                plan = decide(paused_value=value, prs=[pr(9, labels=["upstream-sync"])], remote_branches={PORT_BRANCH})
                self.assertEqual((plan["action"], plan["reason"], plan["branch"]), ("skip", "SYNC_PAUSED is true", ""))

    def test_paused_run_selects_nothing(self):
        def unreachable(_):
            raise AssertionError("a paused run must not inspect history")
        for pause in ({"paused_value": "true"}, {"incidents": [{"number": 5}]}):
            with self.subTest(pause=pause):
                plan = decide(**pause, in_main=unreachable, in_linux_port=unreachable, tags={})
                self.assertEqual((plan["action"], plan["source"]), ("skip", ""))

    def test_open_release_incident_pauses_like_the_variable(self):
        plan = decide(incidents=[{"number": 5}, {"number": 8}], remote_branches={PORT_BRANCH})
        self.assertEqual((plan["action"], plan["branch"]), ("skip", ""))
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

    def test_open_pr_with_either_sync_label_blocks_either_source(self):
        for label in ("upstream-sync", "needs-adaptation"):
            for scenario in ({}, self.FALLBACK):
                with self.subTest(label=label, scenario=scenario):
                    plan = decide(prs=[pr(12, labels=[label])], **scenario)
                    self.assertEqual(plan["action"], "skip")
                    self.assertIn("#12", plan["reason"])

    def test_unlabelled_manual_sync_branch_of_either_source_blocks(self):
        for head in ("sync/umputun-v0.43.0", "sync/linux-port-12e8b468"):
            for scenario in ({}, self.FALLBACK):
                with self.subTest(head=head, scenario=scenario):
                    plan = decide(prs=[pr(494, head=head)], **scenario)
                    self.assertEqual(plan["action"], "skip")
                    self.assertIn("#494", plan["reason"])

    def test_a_blocked_plan_still_names_what_a_dry_run_builds(self):
        plan = decide(prs=[pr(494, head=PORT_BRANCH)], remote_branches={PORT_BRANCH})
        self.assertEqual((plan["action"], plan["source"], plan["commit"], plan["branch"]),
                         ("skip", "linux-port", PORT, PORT_BRANCH))

    def test_unrelated_open_prs_do_not_block(self):
        prs = [pr(1, head="claude/sync-linux-port-493", labels=["bug"]),
               {**pr(2, head="upstream-sync-notes"), "labels": None}]
        self.assertEqual(decide(prs=prs)["action"], "sync")

    def test_a_rehearsal_draft_and_branch_do_not_block_normal_sync(self):
        plan = decide(prs=[pr(42, head=REHEARSAL_BRANCH, labels=["sync-rehearsal"])],
                      remote_branches={REHEARSAL_BRANCH})
        self.assertEqual((plan["action"], plan["branch"]), ("sync", PORT_BRANCH))

    def test_fork_pr_never_blocks_whatever_its_label_or_head(self):
        for label in ("upstream-sync", "needs-adaptation"):
            for head in ("x", PORT_BRANCH, "sync/umputun-v0.43.0"):
                with self.subTest(label=label, head=head):
                    self.assertEqual(decide(prs=[pr(7, head=head, labels=[label], cross=True)])["action"], "sync")

    def test_pr_listing_without_the_cross_repository_field_fails_closed(self):
        for cross in ({}, {"isCrossRepository": None}, {"isCrossRepository": "true"}):
            with self.subTest(cross=cross), self.assertRaises(sync.SyncError):
                decide(prs=[{"number": 3, "headRefName": "x", "labels": [], **cross}])

    def test_malformed_pr_listing_fails_closed(self):
        with self.assertRaises(sync.SyncError):
            decide(prs={"number": 1})

    def test_existing_branch_without_an_open_pr_blocks_the_run(self):
        for scenario, branch in (({}, PORT_BRANCH), (self.FALLBACK, "sync/umputun-v0.43.0")):
            with self.subTest(branch=branch):
                plan = decide(remote_branches={branch}, **scenario)
                self.assertEqual(plan["action"], "blocked")
                self.assertIn(f"branch {branch} exists with no open sync PR", plan["reason"])

    def test_branch_for_another_sync_does_not_block(self):
        others = {"sync/umputun-v0.42.3", "sync/linux-port-2026-11", "sync/linux-port-12e8b468"}
        for scenario in ({}, self.FALLBACK):
            with self.subTest(scenario=scenario):
                self.assertEqual(decide(remote_branches=others, **scenario)["action"], "sync")

    def test_up_to_date_main_skips(self):
        plan = decide(main=[PORT])
        self.assertEqual((plan["action"], plan["source"], plan["commit"], plan["branch"]), ("skip", "", "", ""))

    def test_a_lag_within_the_window_skips_with_the_lag_as_the_reason(self):
        plan = decide(ages={"v0.43.0": sync.DAY}, **self.FALLBACK)
        self.assertEqual((plan["action"], plan["branch"]), ("skip", ""))
        self.assertIn("has lacked v0.43.0 for 1.0 days", plan["reason"])


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

    def test_linux_port_pr_names_the_head_and_the_release_it_contains(self):
        title, label, draft, body = text(source="linux-port", commit=PORT)
        self.assertEqual(title, "Sync melonamin/agterm-linux linux-port eeeeeeee (umputun/agterm v0.43.0)")
        self.assertEqual((label, draft), ("upstream-sync", False))
        self.assertIn(f"https://github.com/melonamin/agterm-linux/commit/{PORT}", body)
        self.assertIn("This linux-port head contains [umputun/agterm v0.43.0]"
                      f"(https://github.com/umputun/agterm/releases/tag/v0.43.0) (`{'c' * 40}`).", body)

    def test_every_body_passes_the_pr_fields_checker(self):
        for source in ("umputun", "linux-port"):
            for conflicts, github in (((), ()), ([("a", "both modified")], ()), ((), [".github/workflows/ci.yml"])):
                with self.subTest(source=source, conflicts=conflicts, github=github):
                    body = text(conflicts, github, head="2" * 40, source=source)[3]
                    self.assertEqual(pr_fields.problems(body, "2" * 40), [])
                    self.assertEqual(pr_fields.values(body)["Task"], ["#476"])

    def test_every_body_names_the_fork_ci_pin_and_the_contained_release(self):
        for source in ("umputun", "linux-port"):
            for conflicts in ((), [("a", "both modified")]):
                with self.subTest(source=source, conflicts=conflicts):
                    body = text(conflicts, source=source)[3]
                    self.assertIn("sets `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to `v0.43.0` / "
                                  f"`{'c' * 40}` in `.github/workflows/omarchy-ci.yml`", body)

    def test_pins_never_move_back_when_main_holds_a_newer_release(self):
        for source in ("umputun", "linux-port"):
            with self.subTest(source=source):
                body = text(source=source, pin=("v0.44.0", "f" * 40))[3]
                self.assertIn(f"keep `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` at `v0.44.0` / `{'f' * 40}` in "
                              "`.github/workflows/omarchy-ci.yml`", body)
                self.assertIn("never move them back to `v0.43.0`", body)
                self.assertNotIn("sets `UPSTREAM_BASE_TAG`", body)

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

    def test_the_variable_then_incidents_decide_the_pause_before_any_upstream_fetch(self):
        variable = self.position('python3 scripts/upstream-sync.py pause --paused "$SYNC_PAUSED" || status=$?')
        self.assertLess(variable, self.position("gh issue list --state open --label release-incident"))
        decided = self.position("--incidents \"$RUNNER_TEMP/incidents.json\"\n")
        for upstream in ("https://github.com/melonamin/agterm-linux.git", "https://github.com/umputun/agterm.git",
                         "'repos/umputun/agterm/releases?per_page=100'", "actions/artifacts?name="):
            self.assertLess(decided, self.position(upstream))
        self.assertEqual(WORKFLOW.count("if: steps.pause.outputs.paused == 'false'"), 4)

    def test_fetches_land_where_the_script_reads_them(self):
        self.position(f"'+refs/heads/{sync.LINUX_PORT_BRANCH}:{sync.LINUX_PORT_REF}'")
        self.position(f"https://github.com/{sync.LINUX_PORT}.git")
        self.position(f"https://github.com/{sync.UPSTREAM}.git '+refs/tags/v*:{sync.TAG_NAMESPACE}/v*'")

    def test_the_stranded_branch_listing_covers_both_sources(self):
        for prefix in sync.BRANCH_PREFIXES.values():
            self.position(f"'refs/heads/{prefix}*'")

    def test_the_plan_reaches_the_merge_for_either_source(self):
        self.position("if: steps.plan.outputs.action == 'sync' || "
                      "(env.DRY_RUN == 'true' && steps.plan.outputs.branch != '')")
        for name in ("SOURCE", "TAG", "COMMIT"):
            self.position(f"          {name}: ${{{{ steps.plan.outputs.{name.lower()} }}}}\n")
        self.position('--source "$SOURCE" --tag "$TAG" --commit "$COMMIT"')

    def test_the_plan_gets_the_run_mode_the_releases_and_the_first_seen_record(self):
        self.position('--tag "$TAG_OVERRIDE" --dry-run "$DRY_RUN" --releases "$RUNNER_TEMP/releases.json"')
        self.position("--jq '.[] | {tag_name, draft, published_at}' | jq -s . > \"$RUNNER_TEMP/releases.json\"")
        self.position('unzip -q "$RUNNER_TEMP/first-seen.zip" first-seen.json -d "$RUNNER_TEMP/seen"')
        self.position('--first-seen "$RUNNER_TEMP/seen/first-seen.json" '
                      '--first-seen-out "$RUNNER_TEMP/seen-out/first-seen.json"')
        self.position("path: ${{ runner.temp }}/seen-out/first-seen.json")

    def test_the_first_seen_record_is_written_by_live_runs_and_read_only_from_main(self):
        self.assertEqual(WORKFLOW.count("upstream-sync-first-seen"), 3)
        self.position("actions/artifacts?name=upstream-sync-first-seen&")
        upload = self.position("          name: upstream-sync-first-seen\n")
        self.assertLess(self.position("if: steps.plan.outcome == 'success' && env.DRY_RUN != 'true'"), upload)
        self.assertLess(self.position("name: Refuse a live run off main"), upload)
        self.assertLess(upload, self.position("          overwrite: true # a re-run attempt uploads the name again"))
        self.position('select((.expired | not) and .workflow_run.head_branch == "main"\n'
                      "              and .workflow_run.head_repository_id == .workflow_run.repository_id)")

    def test_the_settings_guard_runs_as_the_pat_before_the_push(self):
        guard = self.position("python3 scripts/upstream-sync.py guard --workflow-permissions")
        self.assertLess(guard, self.position("run: scripts/upstream-sync-publish.sh"))
        self.assertLess(self.position("    environment: upstream-sync"), guard)
        token = self.position("          GH_TOKEN: ${{ secrets.UPSTREAM_SYNC_TOKEN }}\n")
        self.assertLess(self.position("name: Check the settings that make the push safe"), token)
        self.assertLess(token, guard)
        for setting, saved in (("permissions/workflow", "workflow-permissions"), ("secrets", "secrets")):
            listing = self.position(f'gh api --include "repos/$GH_REPO/actions/{setting}" '
                                    f'> "$RUNNER_TEMP/{saved}.http" || true\n')
            self.assertLess(token, listing)
            self.assertLess(listing, guard)
        self.position('guard --workflow-permissions "$RUNNER_TEMP/workflow-permissions.http" \\\n'
                      '            --secrets "$RUNNER_TEMP/secrets.http"')

    def job(self, name):
        start = WORKFLOW.index(f"\n  {name}:\n")
        following = [WORKFLOW.index(f"\n  {other}:\n") for other in ("test", "sync", "rehearsal", "publish", "blocked")]
        return WORKFLOW[start:min([position for position in following if position > start], default=len(WORKFLOW))]

    def test_a_blocked_sync_reaches_the_issue_and_never_the_push(self):
        blocked_true = "needs.sync.outputs.action == 'sync' && needs.sync.outputs.blocked == 'true' &&"
        blocked_false = "needs.sync.outputs.action == 'sync' && needs.sync.outputs.blocked == 'false' &&"
        self.assertIn(blocked_false, self.job("publish"))
        self.assertIn(blocked_true, self.job("blocked"))
        self.position("      blocked: ${{ steps.merge.outputs.blocked }}\n")
        branch = self.position("if: steps.plan.outputs.action == 'sync' && env.DRY_RUN != 'true' && "
                               "steps.merge.outputs.blocked == 'false'")
        issue = self.position("if: steps.plan.outputs.action == 'sync' && env.DRY_RUN != 'true' && "
                              "steps.merge.outputs.blocked == 'true'")
        self.assertLess(branch, self.position("          name: upstream-sync-branch\n"
                                              "          path: ${{ runner.temp }}/out/\n"))
        self.assertLess(issue, self.position("          path: ${{ runner.temp }}/out/body.md"))
        self.assertEqual(self.job("sync").count("${{ runner.temp }}/out/\n"), 1)

    def test_the_blocked_job_only_writes_issues_and_holds_no_sync_credential(self):
        job = self.job("blocked")
        self.assertIn("    permissions:\n      contents: read\n      issues: write\n    env:", job)
        self.assertIn("run: scripts/upstream-sync-blocked.sh", job)
        self.assertIn("github.ref == 'refs/heads/main' &&\n      (github.event_name == 'schedule' || "
                      "(github.event_name == 'workflow_dispatch' && !inputs.dry_run))", job)
        for absent in ("secrets.", "environment:", "git push", "sync.bundle"):
            self.assertNotIn(absent, job)
        self.assertLess(job.index("name: Create the needs-adaptation label if missing"),
                        job.index("run: scripts/upstream-sync-blocked.sh"))

    def test_the_blocked_script_is_linted_and_dry_runs_its_own_changes(self):
        self.position("      - scripts/upstream-sync-blocked.sh\n")
        self.assertEqual(CI_WORKFLOW.count("            scripts/upstream-sync-blocked.sh\n"), 1)

    def test_rehearsal_keeps_the_planner_and_first_seen_record_out_of_its_path(self):
        rehearsal = self.job("rehearsal")
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.rehearsal", rehearsal)
        self.assertIn("if: github.event_name != 'workflow_dispatch' || !inputs.rehearsal", self.job("sync"))
        for value in ("secrets.", "first-seen", "SYNC_PAUSED", "upstream-sync.py", "issues: write"):
            self.assertNotIn(value, rehearsal)
        publish = self.job("publish")
        self.assertIn("needs.sync.result == 'success'", publish)
        self.assertIn("needs.rehearsal.result == 'success' && github.event_name == 'workflow_dispatch'", publish)
        self.assertIn("inputs.rehearsal && !inputs.dry_run", publish)
        self.assertIn("if: always() && needs.rehearsal.result == 'success'", publish)

    def test_reruns_can_replace_each_publish_bundle(self):
        for job, artifact in (("sync", "upstream-sync-branch"), ("rehearsal", "upstream-sync-rehearsal")):
            with self.subTest(job=job):
                self.assertIn(f"          name: {artifact}\n"
                              "          path: ${{ runner.temp }}/out/\n"
                              "          retention-days: 1\n"
                              "          if-no-files-found: error\n"
                              "          overwrite: true", self.job(job))

    def test_labels_exist_before_the_publish_script_runs(self):
        self.assertLess(self.position("name: Create the sync labels if missing"),
                        self.position("run: scripts/upstream-sync-publish.sh"))
        self.assertNotIn("labels[]=", WORKFLOW)


class RehearsalFixtureTests(unittest.TestCase):
    def test_bundle_contains_only_the_synthetic_change_on_the_checked_out_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

            git("init", "--quiet", "-b", "main")
            workflow = Path(".github/workflows/upstream-sync.yml")
            (repo / workflow).parent.mkdir(parents=True)
            original_workflow = "name: Upstream sync\n"
            (repo / workflow).write_text(original_workflow, encoding="utf-8")
            git("add", str(workflow))
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "--quiet", "-m", "main")
            main = git("rev-parse", "HEAD")
            first_seen = root / "first-seen.json"
            first_seen.write_text('{"sentinel":"keep"}', encoding="utf-8")
            env = dict(os.environ, GITHUB_REPOSITORY=REHEARSAL_REPO, GITHUB_EVENT_NAME="workflow_dispatch",
                       GITHUB_REF="refs/heads/main", GITHUB_RUN_ID="314", GITHUB_RUN_ATTEMPT="2", GITHUB_SHA=main,
                       RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root / "outputs"), DRY_RUN="false", TAG_OVERRIDE="")
            for override in ({"TAG_OVERRIDE": "v1.2.3"}, {"GITHUB_REF": "refs/heads/feature"}, {"DRY_RUN": "true"}):
                rejected = subprocess.run(["bash", str(REHEARSAL)], cwd=repo, env=env | override, capture_output=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(git("rev-parse", "HEAD"), main)
            subprocess.run(["bash", str(REHEARSAL)], cwd=repo, env=env, check=True, capture_output=True)
            head = git("rev-parse", "refs/heads/rehearsal/linux-port-fixture")
            self.assertEqual(git("rev-parse", head + "^"), main)
            self.assertEqual(git("diff", "--name-only", main, head).splitlines(),
                             [str(workflow), "upstream-sync-rehearsal.txt"])
            self.assertEqual((repo / workflow).read_text(encoding="utf-8"),
                             original_workflow + "\n# Synthetic upstream sync publish rehearsal. Never merge.\n")
            self.assertEqual(git("bundle", "list-heads", str(root / "out/sync.bundle")),
                             f"{head} refs/heads/{REHEARSAL_BRANCH}")
            body = (root / "out/body.md").read_text(encoding="utf-8")
            self.assertIn("Never merge", body)
            self.assertIn("Task #476", body)
            self.assertEqual(first_seen.read_text(encoding="utf-8"), '{"sentinel":"keep"}')


STUB = r"""#!/usr/bin/env bash
printf '%s\t%s\t%s\n' "$(basename "$0")" "${GH_TOKEN:-}|${GIT_CONFIG_VALUE_0:-}" "$*" >> "$STUB_LOG"
if [[ "$(basename "$0")" == git ]]; then
  ref="refs/heads/$BRANCH"
  if [[ "$1" == ls-remote ]]; then
    if [[ -s "$STUB_STATE.remote" ]]; then
      printf '%s\t%s\n' "$(< "$STUB_STATE.remote")" "$ref"
    fi
    exit 0
  fi
  if [[ "$*" == *"origin :$ref" ]]; then
    [[ -z "${STUB_DELETE_FAILS:-}" ]] || exit 1
    : > "$STUB_STATE.remote"
    exit 0
  fi
  [[ "$*" == *"$ref:$ref"* ]] || exit 0
  case "${STUB_PUSH:-new}" in
    new) printf '%s\n' "$HEAD_SHA" > "$STUB_STATE.remote"; printf '*\t%s:%s\t[new branch]\n' "$ref" "$ref" ;;
    same) printf '=\t%s:%s\t[up to date]\n' "$ref" "$ref" ;;
    *) printf '!\t%s:%s\t[rejected] (stale info)\n' "$ref" "$ref"; exit 1 ;;
  esac
  exit 0
fi
case "$1 $2" in
  "pr create") [[ "$STUB_CREATE" == ok ]] && { echo "https://github.com/o/r/pull/12"; exit 0; }; exit 1 ;;
  "pr list") [[ -z "${STUB_LIST_FAILS:-}" ]] || exit 1; echo "${STUB_FOUND:-}" ;;
  "pr view")
    [[ "$*" == *" --jq "* ]] || exit 1
    printf '%s\t%s\tfalse\t%s\t%s\n' \
      "$BRANCH" "${STUB_VIEW_HEAD:-$HEAD_SHA}" "${STUB_VIEW_DRAFT:-true}" "${STUB_VIEW_STATE:-OPEN}" ;;
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

    def run_publish(self, *, finish=False, finish_after_publish=False, **stub):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            for tool in ("gh", "git"):
                (root / "bin" / tool).write_text(STUB, encoding="utf-8")
                (root / "bin" / tool).chmod(0o755)
            no_jq = root / "bin/jq"
            no_jq.write_text("#!/usr/bin/env bash\necho 'standalone jq is unavailable' >&2\nexit 127\n",
                             encoding="utf-8")
            no_jq.chmod(0o755)
            (root / "body.md").write_text("body", encoding="utf-8")
            env = {"PATH": f"{root / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(root / "log"),
                   "STUB_STATE": str(root / "state"), "GITHUB_OUTPUT": str(root / "out"),
                   "SYNC_TOKEN": "pat-secret", "GH_REPO": "o/r", "BRANCH": "sync/umputun-v0.43.0",
                   "HEAD_SHA": "2" * 40, "TITLE": "Sync umputun/agterm v0.43.0", "DRAFT": "true",
                   "LABEL": "needs-adaptation", "BODY_FILE": str(root / "body.md"), "LABEL_RETRY_DELAY": "0",
                   **stub}
            env["RUNNER_TEMP"] = str(root)
            if finish:
                (root / "upstream-sync-rehearsal-owned").write_text(stub.get("STUB_OWNERSHIP", "") + "\n")
                (root / "state.remote").write_text(stub.get("STUB_REMOTE_HEAD", env["HEAD_SHA"]) + "\n")
            command = ["bash", str(PUBLISH)]
            result = subprocess.run(command + (["--finish-rehearsal"] if finish else []),
                                    env=env, capture_output=True, text=True)
            if finish_after_publish:
                if result.returncode == 0:
                    env["REHEARSAL_PR"] = (root / "out").read_text().strip().split("=", 1)[1]
                result = subprocess.run(command + ["--finish-rehearsal"], env=env, capture_output=True, text=True)
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

    def test_a_linux_port_branch_publishes_the_same_way(self):
        branch = "sync/linux-port-12e8b468"
        result, calls, output = self.run_publish(STUB_CREATE="ok", BRANCH=branch)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_all_as_pat(calls)
        self.assertEqual(self.args(calls, "git", "push"),
                         [f"push --porcelain --force-with-lease=refs/heads/{branch}: origin "
                          f"refs/heads/{branch}:refs/heads/{branch}"])
        self.assertIn(f"--head {branch} ", self.args(calls, "gh", "pr create")[0])
        self.assertEqual(output, "number=12\n")

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

    REHEARSAL_ENV = {
        "REHEARSAL": "true", "GH_REPO": REHEARSAL_REPO, "BRANCH": REHEARSAL_BRANCH, "GITHUB_RUN_ID": "314",
        "GITHUB_RUN_ATTEMPT": "2", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "TITLE": "[rehearsal] Upstream sync publish", "LABEL": "sync-rehearsal",
    }

    def test_rehearsal_uses_the_same_publish_path_and_withdraws_its_own_draft(self):
        result, calls, output = self.run_publish(STUB_CREATE="ok", finish_after_publish=True, **self.REHEARSAL_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_all_as_pat(calls)
        create = self.args(calls, "gh", "pr create")
        self.assertEqual(len(create), 1)
        self.assertIn("--label sync-rehearsal", create[0])
        self.assertIn("--draft", create[0])
        self.assertEqual(len(self.args(calls, "gh", "pr close 12")), 1)
        self.assertEqual(calls[-1][2], f"push --force-with-lease=refs/heads/{REHEARSAL_BRANCH}:{'2' * 40} "
                                       f"origin :refs/heads/{REHEARSAL_BRANCH}")
        self.assertEqual(output, "number=12\n")

    def test_missing_or_wrong_ownership_prevents_rehearsal_cleanup(self):
        result, calls, _ = self.run_publish(finish=True, STUB_OWNERSHIP="1" * 40, **self.REHEARSAL_ENV)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_rehearsal_refuses_a_real_sync_branch_before_using_the_token(self):
        env = self.REHEARSAL_ENV | {"BRANCH": PORT_BRANCH}
        result, calls, _ = self.run_publish(**env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_rehearsal_cleanup_leaves_a_moved_branch_and_pr_alone(self):
        result, calls, _ = self.run_publish(finish=True, STUB_OWNERSHIP="2" * 40, STUB_REMOTE_HEAD="1" * 40,
                                            **self.REHEARSAL_ENV)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([(tool, args) for tool, _, args in calls],
                         [("git", f"ls-remote --refs origin refs/heads/{REHEARSAL_BRANCH}")])

    def test_failed_rehearsal_close_still_attempts_only_the_owned_branch_delete(self):
        result, calls, _ = self.run_publish(STUB_CREATE="ok", finish_after_publish=True, STUB_CLOSE_FAILS="1",
                                            **self.REHEARSAL_ENV)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"origin :refs/heads/{REHEARSAL_BRANCH}", calls[-1][2])

    def test_missing_inputs_fail_before_any_call(self):
        result, calls, _ = self.run_publish(STUB_CREATE="ok", SYNC_TOKEN="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])


BLOCKED_STUB = r"""#!/usr/bin/env bash
printf '%s\t%s\n' "${GH_TOKEN:-}" "$*" >> "$STUB_LOG"
case "$1 $2" in
  "issue list") [[ -z "${STUB_LIST_FAILS:-}" ]] || exit 1; printf '%s' "${STUB_ISSUES:-}" ;;
  "issue create") [[ -z "${STUB_CREATE_FAILS:-}" ]] || exit 1; echo "https://github.com/o/r/issues/77" ;;
  "issue edit") [[ -z "${STUB_EDIT_FAILS:-}" ]] || exit 1 ;;
esac
"""
BLOCKED_ISSUE = "Upstream sync blocked: new workflow file(s) from linux-port@12e8b468"


class BlockedScriptTests(unittest.TestCase):
    """The blocked step against a stubbed gh: one issue, created once and rewritten after."""

    def run_blocked(self, **stub):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "bin" / "gh").write_text(BLOCKED_STUB, encoding="utf-8")
            (root / "bin" / "gh").chmod(0o755)
            (root / "body.md").write_text("body", encoding="utf-8")
            env = {"PATH": f"{root / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(root / "log"),
                   "GITHUB_OUTPUT": str(root / "out"), "GH_TOKEN": "run-token", "GH_REPO": "o/r",
                   "TITLE": BLOCKED_ISSUE, "LABEL": "needs-adaptation", "BODY_FILE": str(root / "body.md"), **stub}
            result = subprocess.run(["bash", str(BLOCKED)], env=env, capture_output=True, text=True)
            log = (root / "log").read_text(encoding="utf-8").splitlines() if (root / "log").exists() else []
            output = (root / "out").read_text(encoding="utf-8") if (root / "out").exists() else ""
            return result, [line.split("\t", 1)[1] for line in log], output, str(root / "body.md")

    def test_the_title_prefix_is_the_one_the_planner_writes(self):
        self.assertIn(f"PREFIX='{sync.BLOCKED_TITLE}'", BLOCKED.read_text(encoding="utf-8"))
        self.assertTrue(BLOCKED_ISSUE.startswith(sync.BLOCKED_TITLE))

    def test_no_open_issue_creates_one_with_the_label_and_warns_without_failing(self):
        result, calls, output, body = self.run_blocked()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0], "issue list --state open --label needs-adaptation --limit 1000 --json number,title "
                                   "--jq sort_by(.number) | .[] | [.number, .title] | @tsv")
        self.assertEqual(calls[1:],
                         [f"issue create --title {BLOCKED_ISSUE} --label needs-adaptation --body-file {body}"])
        self.assertEqual(output, "number=77\n")
        self.assertIn(f"::warning::{BLOCKED_ISSUE}; nothing was pushed, see https://github.com/o/r/issues/77",
                      result.stdout)
        self.assertNotIn("::error::", result.stdout + result.stderr)

    def test_an_open_blocked_issue_is_rewritten_not_duplicated(self):
        issues = ("12\tSync melonamin/agterm-linux linux-port 12e8b468\n"
                  "31\tUpstream sync blocked: new workflow file(s) from linux-port@aaaaaaaa\n"
                  "40\tUpstream sync blocked: new workflow file(s) from umputun@bbbbbbbb\n")
        result, calls, output, body = self.run_blocked(STUB_ISSUES=issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1:], [f"issue edit 31 --title {BLOCKED_ISSUE} --body-file {body}"])
        self.assertEqual(output, "number=31\n")
        self.assertIn("https://github.com/o/r/issues/31", result.stdout)

    def test_other_labelled_issues_do_not_stand_in(self):
        issues = "12\tNot Upstream sync blocked: new workflow file(s) from x\n13\tupstream sync blocked\n"
        _, calls, output, _ = self.run_blocked(STUB_ISSUES=issues)
        self.assertTrue(calls[1].startswith("issue create "))
        self.assertEqual(output, "number=77\n")

    def test_a_failed_listing_create_or_edit_fails_the_step(self):
        blocked = "31\tUpstream sync blocked: new workflow file(s) from linux-port@aaaaaaaa\n"
        for stub, made in (({"STUB_LIST_FAILS": "1"}, 1), ({"STUB_CREATE_FAILS": "1"}, 2),
                           ({"STUB_ISSUES": blocked, "STUB_EDIT_FAILS": "1"}, 2)):
            with self.subTest(stub=stub):
                result, calls, output, _ = self.run_blocked(**stub)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((len(calls), output), (made, ""))
                self.assertNotIn("::warning::", result.stdout)

    def test_a_title_that_is_not_a_blocked_title_or_a_missing_input_makes_no_call(self):
        for stub in ({"TITLE": "Sync umputun/agterm v0.43.0"}, {"TITLE": ""}, {"LABEL": ""}, {"GH_TOKEN": ""},
                     {"BODY_FILE": ""}):
            with self.subTest(stub=stub):
                result, calls, _, _ = self.run_blocked(**stub)
                self.assertEqual((result.returncode, calls), (1, []))


class ScheduleLivenessTests(unittest.TestCase):
    NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)

    def history(self, age=None):
        if age is None:
            return {"total_count": 0, "workflow_runs": []}
        created = self.NOW - age
        return {"total_count": 1, "workflow_runs": [{
            "id": 3, "event": "schedule", "head_branch": "main",
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "run_started_at": self.NOW.isoformat(), "conclusion": "failure",
        }]}

    def issue(self, state):
        return {"number": 26, "title": liveness.TITLE, "state": state}

    def reconcile(self, age=None, issues=(), responses=()):
        client = mock.Mock(side_effect=[json.dumps(self.history(age)), json.dumps(issues), *responses])
        message = liveness.reconcile(client, self.NOW)
        queries = [call.args for call in client.call_args_list]
        self.assertEqual(queries[0], (
            "api", f"repos/{liveness.REPOSITORY}/actions/workflows/upstream-sync.yml/runs"
                   "?branch=main&event=schedule&per_page=1"))
        self.assertEqual(queries[1][:6], ("issue", "list", "--state", "all", "--label", liveness.LABEL))
        return message, queries[2:]

    def test_no_schedule_history_is_stale(self):
        self.assertEqual(liveness.schedule_status(self.history(), self.NOW), (True, None))

    def test_uses_created_at_and_is_stale_at_exactly_twelve_hours(self):
        for seconds, stale in ((43199, False), (43200, True), (43201, True)):
            with self.subTest(seconds=seconds):
                result, run = liveness.schedule_status(self.history(timedelta(seconds=seconds)), self.NOW)
                self.assertEqual(result, stale)
                self.assertEqual(run, (3, self.NOW - timedelta(seconds=seconds)))

    def test_wrong_event_branch_or_future_creation_is_rejected(self):
        for changes in ({"event": "push"}, {"head_branch": "feature"},
                        {"created_at": (self.NOW + timedelta(seconds=1)).isoformat()}):
            with self.subTest(changes=changes):
                history = self.history(timedelta(seconds=1))
                history["workflow_runs"][0].update(changes)
                with self.assertRaises(ValueError):
                    liveness.schedule_status(history, self.NOW)

    def test_api_failure_never_changes_an_issue(self):
        client = mock.Mock(side_effect=subprocess.CalledProcessError(1, ["gh", "api"]))
        with self.assertRaises(subprocess.CalledProcessError):
            liveness.reconcile(client, self.NOW)
        client.assert_called_once()

    def test_first_alarm_creates_one_labelled_issue(self):
        existing = subprocess.CalledProcessError(1, ["gh", "api"], stderr="already_exists")
        message, writes = self.reconcile(responses=(existing, "https://github.com/example/issues/26"))
        self.assertIn("Schedule is stale", message)
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[1][:6], ("issue", "create", "--title", liveness.TITLE, "--label", liveness.LABEL))

    def test_later_alarm_edits_or_reopens_the_same_issue(self):
        for state, expected, responses in (("OPEN", ("edit",), ("",)),
                                            ("CLOSED", ("edit", "reopen"), ("", ""))):
            with self.subTest(state=state):
                _, writes = self.reconcile(issues=[self.issue(state)], responses=responses)
                self.assertEqual(tuple(query[1] for query in writes), expected)
                self.assertTrue(all(query[2] == "26" for query in writes))

    def test_a_renamed_labelled_issue_is_reused(self):
        _, writes = self.reconcile(issues=[self.issue("OPEN") | {"title": "Renamed"}], responses=("",))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:5], ("issue", "edit", "26", "--title", liveness.TITLE))

    def test_recent_scheduled_run_closes_the_alarm_even_if_the_run_failed(self):
        message, writes = self.reconcile(timedelta(hours=2), [self.issue("OPEN")], ("",))
        self.assertIn("Schedule is live", message)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:4], ("issue", "close", "26", "--comment"))
        self.assertIn("/actions/runs/3", writes[0][4])

    def test_healthy_checks_leave_closed_or_absent_issues_alone(self):
        for issues in ([], [self.issue("CLOSED")]):
            with self.subTest(issues=issues):
                _, writes = self.reconcile(timedelta(hours=1), issues)
                self.assertEqual(writes, [])

    def test_duplicate_alarm_records_are_left_alone(self):
        client = mock.Mock(side_effect=[json.dumps(self.history()),
                                       json.dumps([self.issue("OPEN"), self.issue("CLOSED")])])
        with self.assertRaisesRegex(ValueError, "multiple schedule alarm issues"):
            liveness.reconcile(client, self.NOW)
        self.assertEqual(client.call_count, 2)

    def test_workflow_checks_pushes_even_when_no_schedule_is_delivered(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/upstream-sync-liveness.yml").read_text()
        self.assertIn("  push:\n    branches: [main]\n", workflow)
        self.assertIn("  group: upstream-sync-liveness\n  cancel-in-progress: false", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertNotIn("pull_request", workflow)
        self.assertNotIn("secrets.", workflow)


class GitRepoTests(unittest.TestCase):
    """Real git: tag peeling, the ancestor check, source selection, and both merge outcomes."""

    COMMITTED = 1_600_000_000  # every commit and tag here is this old; only a release or a sighting sets a clock

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
        self.git("checkout", "-q", "-b", "linux-port")
        self.commit("port-only.txt", "port\n", "linux adaptation")
        self.port = self.head()
        self.git("checkout", "-q", "main")
        self.commit("fork-only.txt", "fork\n", "fork file")
        self.git("tag", "-a", "-m", "release", "annotated", self.upstream)
        self.git("update-ref", "refs/upstream-tags/v0.2.0", self.git("rev-parse", "refs/tags/annotated"))
        self.git("tag", "-d", "annotated")
        self.git("update-ref", "refs/upstream-tags/v0.1.0", self.base_tag)
        self.git("update-ref", sync.LINUX_PORT_REF, self.port)
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    DATES = {"GIT_COMMITTER_DATE": f"{COMMITTED} +0000", "GIT_AUTHOR_DATE": f"{COMMITTED} +0000"}

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "tag.gpgSign=false",
                               "-c", "commit.gpgSign=false", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True, env={**os.environ, **self.DATES}).stdout.strip()

    def commit(self, name, text, message):
        (self.repo / name).write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def plan(self, *extra, prs="[]", branches="", incidents="[]", releases=("v0.1.0", "v0.2.0")):
        """(exit status, the plan) of the `plan` command; `releases` are published at PUBLISHED."""
        listing = json.dumps([release(tag) for tag in releases]) if isinstance(releases, tuple) else releases
        paths = {name: self.root / name for name in ("prs.json", "branches.txt", "incidents.json", "releases.json")}
        for path, content in zip(paths.values(), (prs, branches, incidents, listing), strict=True):
            path.write_text(content, encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "out")}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = sync.main(["plan", "--base", "main", "--open-prs", str(paths["prs.json"]), "--remote-branches",
                              str(paths["branches.txt"]), "--incidents", str(paths["incidents.json"]),
                              "--releases", str(paths["releases.json"]), *extra])
        return code, json.loads(out.getvalue()) if out.getvalue() else {}

    def merge(self, *args, base="main"):
        """(exit status, the outputs, the body) of the `merge` command against this repository."""
        body, out = self.root / "body.md", io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "out")}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = sync.main(["merge", "--base", base, "--worktree", str(self.root / "wt"), "--body", str(body),
                              "--run-url", RUN, *args])
        return (code, json.loads(out.getvalue()) if out.getvalue() else {},
                body.read_text(encoding="utf-8") if body.exists() else "")

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

    def test_plan_command_selects_the_linux_port_head_main_lacks(self):
        code, plan = self.plan(branches=f"{'0' * 40}\trefs/heads/sync/linux-port-2026-11\n")
        self.assertEqual(code, 0)
        self.assertEqual((plan["action"], plan["source"], plan["tag"], plan["commit"], plan["branch"]),
                         ("sync", "linux-port", "", self.port, f"sync/linux-port-{self.port[:8]}"))
        self.assertIn("action=sync\n", (self.root / "out").read_text(encoding="utf-8"))

    def test_plan_command_falls_back_to_the_release_a_merged_linux_port_lags(self):
        self.git("update-ref", sync.LINUX_PORT_REF, self.base_tag)
        code, plan = self.plan("--now", str(PUBLISHED + LAG))
        self.assertEqual((code, plan["action"], plan["source"]), (0, "skip", ""))
        self.assertIn("has lacked v0.2.0 for 7.0 days; the direct sync waits", plan["reason"])
        code, plan = self.plan("--now", str(PUBLISHED + LAG + 1))
        self.assertEqual(code, 0)
        self.assertEqual((plan["action"], plan["source"], plan["tag"], plan["commit"], plan["branch"]),
                         ("sync", "umputun", "v0.2.0", self.upstream, "sync/umputun-v0.2.0"))

    def test_plan_command_measures_the_lag_at_the_clock_by_default(self):
        self.git("update-ref", sync.LINUX_PORT_REF, self.base_tag)
        with mock.patch.object(sync.time, "time", return_value=PUBLISHED + LAG + 1.9):
            self.assertEqual(self.plan()[1]["source"], "umputun")
        with mock.patch.object(sync.time, "time", return_value=PUBLISHED + sync.DAY):
            self.assertEqual(self.plan()[1]["source"], "")

    def lightweight_release_on_an_old_commit(self):
        """v0.3.0 with no GitHub release, above a linux-port head that main already has."""
        self.git("merge", "-q", "--no-edit", self.upstream)
        self.git("update-ref", sync.LINUX_PORT_REF, self.upstream)
        self.git("checkout", "-q", "-b", "next", self.upstream)
        self.commit("next.txt", "next\n", "the next upstream release")
        self.git("update-ref", "refs/upstream-tags/v0.3.0", self.head())
        self.git("checkout", "-q", "main")
        self.assertEqual(self.git("for-each-ref", "--format=%(objecttype) %(creatordate:unix)",
                                  "refs/upstream-tags/v0.3.0"), f"commit {self.COMMITTED}")

    def test_a_new_lightweight_tag_on_an_old_commit_waits_from_its_first_sighting(self):
        self.lightweight_release_on_an_old_commit()
        seen, first = self.root / "seen" / "first-seen.json", NOW
        record = ["--first-seen", str(seen), "--first-seen-out", str(seen)]
        code, plan = self.plan("--now", str(first), *record)
        self.assertEqual((code, plan["source"]), (0, ""))
        self.assertIn("has lacked v0.3.0 for 0.0 days; the direct sync waits", plan["reason"])
        entry = {"v0.3.0": {"commit": self.git("rev-parse", "next"), "seen": first}}
        self.assertEqual(json.loads(seen.read_text(encoding="utf-8")), entry)
        code, plan = self.plan("--now", str(first + LAG), *record)
        self.assertEqual(plan["source"], "")
        self.assertEqual(json.loads(seen.read_text(encoding="utf-8")), entry)
        code, plan = self.plan("--now", str(first + LAG + 1), *record)
        self.assertEqual((plan["source"], plan["tag"]), ("umputun", "v0.3.0"))
        self.assertIn("has lacked v0.3.0 for 7.0 days, more than 7", plan["reason"])

    def test_a_lightweight_tag_moved_to_another_commit_waits_again(self):
        self.lightweight_release_on_an_old_commit()
        seen = self.root / "seen" / "first-seen.json"
        record = ["--first-seen", str(seen), "--first-seen-out", str(seen)]
        self.plan("--now", str(NOW), *record)
        self.git("checkout", "-q", "next")
        self.commit("moved.txt", "moved\n", "the commit the tag moves to")
        moved = self.head()
        self.git("update-ref", "refs/upstream-tags/v0.3.0", moved)
        self.git("checkout", "-q", "main")
        later = NOW + LAG + 1
        code, plan = self.plan("--now", str(later), *record)
        self.assertEqual((code, plan["source"]), (0, ""))
        self.assertIn("has lacked v0.3.0 for 0.0 days; the direct sync waits", plan["reason"])
        self.assertEqual(json.loads(seen.read_text(encoding="utf-8")), {"v0.3.0": {"commit": moved, "seen": later}})
        code, plan = self.plan("--now", str(later + LAG + 1), *record)
        self.assertEqual((plan["source"], plan["tag"], plan["commit"]), ("umputun", "v0.3.0", moved))

    def test_a_lost_first_seen_record_restarts_the_clock(self):
        self.lightweight_release_on_an_old_commit()
        for record in ([], ["--first-seen", str(self.root / "missing.json")]):
            with self.subTest(record=record):
                self.assertEqual(self.plan("--now", str(NOW + 30 * sync.DAY), *record)[1]["source"], "")

    def test_a_release_published_after_an_old_tagged_commit_sets_the_clock(self):
        self.git("update-ref", sync.LINUX_PORT_REF, self.base_tag)
        self.assertEqual(self.plan("--now", str(PUBLISHED + sync.DAY))[1]["source"], "")
        self.assertEqual(self.plan("--now", str(PUBLISHED + LAG + 1))[1]["source"], "umputun")

    def test_plan_command_fails_closed_without_a_readable_release_listing(self):
        for listing in ("not json", '{"message": "Not Found"}'):
            with self.subTest(listing=listing):
                self.assertEqual(self.plan(releases=listing)[0], 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sync.main(["plan", "--base", "main", "--open-prs", "x", "--remote-branches", "x", "--incidents", "x"])

    def test_plan_command_fails_closed_on_a_malformed_first_seen_record(self):
        seen = self.root / "first-seen.json"
        for record in ('{"v0.3.0": "yesterday"}', '{"v0.3.0": 1800000000}', "not json"):
            with self.subTest(record=record):
                seen.write_text(record, encoding="utf-8")
                self.assertEqual(self.plan("--first-seen", str(seen))[0], 2)

    def test_plan_command_override_is_free_only_in_a_dry_run(self):
        now = ["--now", str(PUBLISHED)]
        code, plan = self.plan("--tag", "v0.2.0", "--dry-run", "true", *now)
        self.assertEqual((code, plan["action"], plan["source"], plan["branch"]),
                         (0, "sync", "umputun", "sync/umputun-v0.2.0"))
        for live in ([], ["--dry-run", "false"], ["--dry-run", ""]):
            with self.subTest(live=live):
                code, plan = self.plan("--tag", "v0.2.0", *live, *now)
                self.assertEqual((code, plan["action"], plan["source"]), (0, "skip", ""))
                self.assertIn("syncs before v0.2.0", plan["reason"])

    def test_plan_command_live_override_still_waits_out_the_lag(self):
        self.git("update-ref", sync.LINUX_PORT_REF, self.base_tag)
        self.assertEqual(self.plan("--tag", "v0.2.0", "--now", str(PUBLISHED + LAG))[1]["source"], "")
        self.assertEqual(self.plan("--tag", "v0.2.0", "--now", str(PUBLISHED + LAG + 1))[1]["tag"], "v0.2.0")

    def test_plan_command_fails_without_the_linux_port_head(self):
        self.git("update-ref", "-d", sync.LINUX_PORT_REF)
        self.assertEqual(self.plan()[0], 2)

    def test_plan_command_honours_an_open_release_incident(self):
        code, plan = self.plan(incidents='[{"number": 482}]')
        self.assertEqual((code, plan["action"], plan["source"]), (0, "skip", ""))
        self.assertIn("#482", plan["reason"])

    def test_plan_command_requires_the_incident_listing(self):
        prs, branches, releases = self.root / "prs.json", self.root / "branches.txt", self.root / "releases.json"
        prs.write_text("[]", encoding="utf-8")
        branches.write_text("", encoding="utf-8")
        releases.write_text("[]", encoding="utf-8")
        for extra in ([], ["--incidents", str(self.root / "missing.json")]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = sync.main(["plan", "--base", "main", "--open-prs", str(prs), "--releases", str(releases),
                                      "--remote-branches", str(branches), *extra])
                except SystemExit as exit_:
                    code = exit_.code
                self.assertEqual(code, 2)

    def test_plan_command_fails_closed_on_unreadable_pr_listing(self):
        self.assertEqual(self.plan(prs="not json")[0], 2)

    def test_clean_merge_is_a_two_parent_commit(self):
        self.git("checkout", "-q", "-b", "nonconflicting", self.base_tag)
        self.commit("other.txt", "x\n", "upstream file only")
        clean = self.head()
        self.git("checkout", "-q", "main")
        built = sync.build(self.root / "wt", self.head(), "sync/umputun-v0.3.0", clean, "upstream v0.3.0")
        self.assertEqual(built, ([], [], []))
        parents = self.git("rev-list", "--parents", "-n", "1", "sync/umputun-v0.3.0").split()[1:]
        self.assertEqual(parents, [self.head(), clean])
        self.assertEqual(self.git("log", "-1", "--format=%s", "sync/umputun-v0.3.0"), "Merge upstream v0.3.0 into main")

    def test_conflicts_are_committed_with_markers_and_both_parents(self):
        self.commit("shared.txt", "fork\n", "fork change")
        main = self.head()
        conflicts, _, _ = sync.build(self.root / "wt", main, "sync/umputun-v0.2.0", self.upstream, "upstream v0.2.0")
        self.assertEqual(conflicts, [("shared.txt", "both modified")])
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", "sync/umputun-v0.2.0").split()[1:],
                         [main, self.upstream])
        self.assertIn("<<<<<<<", self.git("show", "sync/umputun-v0.2.0:shared.txt"))
        self.assertEqual(self.git("show", "sync/umputun-v0.2.0:upstream-only.txt"), "new")
        self.assertEqual(self.git("-C", str(self.root / "wt"), "status", "--porcelain"), "")
        self.assertEqual(self.head(), main)

    CI = ".github/workflows/ci.yml"

    def workflow_branches(self, on_main=None, on_upstream=None):
        """(main, upstream) commits forked from a base that carries ci.yml; each side then runs its edit."""
        self.git("checkout", "-q", "-b", "shared", self.base_tag)
        (self.repo / ".github/workflows").mkdir(parents=True)
        self.commit(self.CI, "on: push\njobs: {}\n", "a workflow both sides have")
        heads = []
        for branch, edit in (("wf-main", on_main), ("wf-upstream", on_upstream)):
            self.git("checkout", "-q", "-b", branch, "shared")
            self.commit(f"{branch}.txt", "x\n", f"{branch} file")
            if edit:
                edit()
                self.git("add", "-A")
                self.git("commit", "-q", "-m", f"{branch} workflow edit")
            heads.append(self.head())
        self.git("checkout", "-q", "main")
        return heads

    def write(self, path, text):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text, encoding="utf-8")

    def built(self, on_main=None, on_upstream=None):
        main, upstream = self.workflow_branches(on_main, on_upstream)
        return sync.build(self.root / "wt", main, "sync/umputun-v0.4.0", upstream, "upstream v0.4.0")

    def test_a_workflow_at_a_path_main_lacks_is_a_new_workflow(self):
        path = ".github/workflows/upstream.yml"
        _, github, new = self.built(on_upstream=lambda: self.write(path, "on: push\n"))
        self.assertEqual((github, new), ([path], [path]))

    def test_a_nested_path_under_workflows_counts_as_new_too(self):
        path = ".github/workflows/nested/upstream.yaml"
        self.assertEqual(self.built(on_upstream=lambda: self.write(path, "on: push\n"))[2], [path])

    def test_a_modified_existing_workflow_is_a_github_change_but_not_new(self):
        _, github, new = self.built(on_upstream=lambda: self.write(self.CI, "on: pull_request\njobs: {}\n"))
        self.assertEqual((github, new), ([self.CI], []))

    def test_other_new_files_under_github_are_not_new_workflows(self):
        def edit():
            self.write(".github/dependabot.yml", "version: 2\n")
            self.write(".github/workflows-notes/readme.yml", "on: push\n")
            self.write("docs/.github/workflows/example.yml", "on: push\n")
        _, github, new = self.built(on_upstream=edit)
        self.assertEqual((github, new), ([".github/dependabot.yml", ".github/workflows-notes/readme.yml"], []))

    def test_a_renamed_workflow_is_new_at_its_new_path(self):
        _, _, new = self.built(on_upstream=lambda: self.git("mv", self.CI, ".github/workflows/renamed.yml"))
        self.assertEqual(new, [".github/workflows/renamed.yml"])

    def test_a_workflow_main_deleted_stays_deleted_and_is_not_new(self):
        conflicts, github, new = self.built(on_main=lambda: self.git("rm", "-q", self.CI))
        self.assertEqual((conflicts, github, new), ([], [], []))

    def test_a_workflow_main_deleted_and_upstream_changed_comes_back_as_new(self):
        conflicts, _, new = self.built(on_main=lambda: self.git("rm", "-q", self.CI),
                                       on_upstream=lambda: self.write(self.CI, "on: pull_request\njobs: {}\n"))
        self.assertEqual((conflicts, new), ([(self.CI, "deleted by us")], [self.CI]))

    def test_merge_command_blocks_a_sync_that_adds_a_workflow_file(self):
        new = ".github/workflows/upstream.yml"
        _, upstream = self.workflow_branches(on_upstream=lambda: self.write(new, "on: push\n"))
        for source, extra in (("linux-port", []), ("umputun", ["--tag", "v0.5.0"])):
            with self.subTest(source=source):
                self.git("update-ref", "refs/upstream-tags/v0.5.0", upstream)
                self.git("worktree", "prune")
                code, result, body = self.merge("--source", source, "--branch", f"sync/{source}-blocked",
                                                "--commit", upstream, *extra, base="wf-main")
                self.assertEqual(code, 0)
                self.assertEqual((result["blocked"], result["new_workflows"], result["label"]),
                                 ("true", 1, "needs-adaptation"))
                self.assertEqual(result["title"],
                                 f"Upstream sync blocked: new workflow file(s) from {source}@{upstream[:8]}")
                self.assertIn(f"## New workflow files (1)\n\n- `{new}`\n", body)
                self.assertIn("not pushed: no branch, no PR", body)
                self.assertNotIn("Reviewed head:", body)
                shutil.rmtree(self.root / "wt")

    def test_merge_command_opens_a_draft_for_a_modified_existing_workflow(self):
        _, upstream = self.workflow_branches(on_upstream=lambda: self.write(self.CI, "on: pull_request\njobs: {}\n"))
        code, result, body = self.merge("--source", "linux-port", "--branch", "sync/linux-port-modified",
                                        "--commit", upstream, base="wf-main")
        self.assertEqual(code, 0)
        self.assertEqual((result["blocked"], result["new_workflows"], result["label"], result["draft"]),
                         ("false", 0, "needs-adaptation", "true"))
        self.assertTrue(result["title"].startswith("Sync melonamin/agterm-linux linux-port "))
        self.assertIn(f"## Changes under .github/ (1)", body)
        self.assertEqual(pr_fields.problems(body, result["head"]), [])

    def commit_boundary_pins(self):
        release = f'env:\n  UPSTREAM_BASE_TAG: "v0.1.0"\n  UPSTREAM_BASE_COMMIT: "{self.base_tag}"\n'
        linux = f'  LINUX_PORT_BASE_COMMIT: "{self.base_tag}"\n'
        self.write(sync.CI_WORKFLOW, release + linux)
        self.write(".github/workflows/release-linux.yml", release)
        self.write(self.CI, "name: inherited upstream\n")
        self.git("add", ".github/workflows")
        self.git("commit", "-q", "-m", "add the fork and inherited CI layout")

    def assert_boundary_pins(self, branch, linux_port):
        content = self.git("show", f"{branch}:{sync.CI_WORKFLOW}")
        self.assertIn('UPSTREAM_BASE_TAG: "v0.2.0"', content)
        self.assertIn(f'UPSTREAM_BASE_COMMIT: "{self.upstream}"', content)
        self.assertIn(f'LINUX_PORT_BASE_COMMIT: "{linux_port}"', content)
        inherited_release = self.git("show", f"{branch}:.github/workflows/release-linux.yml")
        self.assertIn(f'UPSTREAM_BASE_COMMIT: "{self.base_tag}"', inherited_release)
        self.assertEqual(self.git("show", f"{branch}:{self.CI}"), "name: inherited upstream")

    def test_automatic_fallback_advances_only_the_umputun_pins(self):
        self.commit_boundary_pins()
        branch = "sync/umputun-v0.2.0"
        code, result, _ = self.merge("--source", "umputun", "--branch", branch, "--tag", "v0.2.0",
                                     "--commit", self.upstream)
        self.assertEqual((code, result["draft"], result["github_changes"]), (0, "false", 0))
        self.assert_boundary_pins(branch, self.base_tag)

    def test_a_merge_that_records_no_upstream_parent_fails(self):
        with self.assertRaisesRegex(sync.SyncError, "parents"):
            sync.build(self.root / "wt", self.head(), "sync/umputun-v0.1.0", self.base_tag, "upstream v0.1.0")

    def test_merge_command_writes_a_body_the_checker_accepts(self):
        code, result, body = self.merge("--source", "umputun", "--branch", "sync/umputun-v0.2.0", "--tag", "v0.2.0",
                                        "--commit", self.upstream)
        self.assertEqual((code, result["title"]), (0, "Sync umputun/agterm v0.2.0"))
        self.assertEqual(result["head"], self.git("rev-parse", "sync/umputun-v0.2.0"))
        self.assertEqual(pr_fields.problems(body, result["head"]), [])
        self.assertIn(f"to `v0.2.0` / `{self.upstream}`", body)

    def test_merge_command_rejects_a_tag_that_is_not_at_the_commit(self):
        for tag in ("v0.1.0", "v0.9.0", ""):
            with self.subTest(tag=tag):
                self.assertEqual(self.merge("--source", "umputun", "--branch", "sync/umputun-x", "--tag", tag,
                                            "--commit", self.upstream)[0], 2)
        self.assertEqual(self.git("branch", "--list", "sync/*"), "")

    def test_linux_port_merge_names_the_release_linux_port_contains_as_the_base_pin(self):
        self.commit_boundary_pins()
        branch = f"sync/linux-port-{self.port[:8]}"
        code, result, body = self.merge("--source", "linux-port", "--branch", branch, "--commit", self.port)
        self.assertEqual(code, 0)
        self.assertEqual(result["title"],
                         f"Sync melonamin/agterm-linux linux-port {self.port[:8]} (umputun/agterm v0.2.0)")
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", branch).split()[1:], [self.head(), self.port])
        self.assertEqual(self.git("log", "-1", "--format=%s", branch),
                         f"Merge melonamin/agterm-linux linux-port {self.port[:8]} into main")
        self.assertEqual(self.git("show", f"{branch}:port-only.txt"), "port")
        self.assertEqual(pr_fields.problems(body, result["head"]), [])
        self.assertIn(f"sets `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to `v0.2.0` / `{self.upstream}`", body)
        self.assert_boundary_pins(branch, self.port)

    def test_linux_port_merge_keeps_the_pins_at_a_newer_release_main_already_has(self):
        self.git("merge", "-q", "--no-edit", self.upstream)
        self.commit_boundary_pins()
        self.git("checkout", "-q", "-b", "old-port", self.base_tag)
        self.commit("port-only.txt", "port\n", "linux adaptation on the old release")
        port = self.head()
        self.git("checkout", "-q", "main")
        branch = f"sync/linux-port-{port[:8]}"
        code, result, body = self.merge("--source", "linux-port", "--branch", branch,
                                        "--commit", port)
        self.assertEqual(code, 0)
        self.assertIn("(umputun/agterm v0.1.0)", result["title"])
        self.assertIn(f"keep `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` at `v0.2.0` / `{self.upstream}`", body)
        self.assertIn("never move them back to `v0.1.0`", body)
        self.assert_boundary_pins(branch, port)

    def test_linux_port_merge_fails_when_the_head_contains_no_release(self):
        self.git("checkout", "-q", "--orphan", "unrelated")
        self.commit("alone.txt", "x\n", "unrelated history")
        unrelated = self.head()
        self.git("checkout", "-q", "main")
        code, _, _ = self.merge("--source", "linux-port", "--branch", "sync/linux-port-x", "--commit", unrelated)
        self.assertEqual(code, 2)
        self.assertEqual(self.git("branch", "--list", "sync/*"), "")


if __name__ == "__main__":
    unittest.main()
