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
PORT = "e" * 40  # the linux-port head
PORT_BRANCH = "sync/linux-port-eeeeeeee"
NOW = 1_800_000_000
LAG = sync.LINUX_PORT_LAG_DAYS * sync.DAY
BEHIND = ("a" * 40, "b" * 40, "d" * 40)  # a linux-port that carries every release but v0.43.0


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
                self.assertEqual((clocks["v0.43.0"], kept["v0.43.0"]), (NOW, NOW))

    def test_a_recorded_first_sighting_is_kept_and_a_lost_one_restarts_now(self):
        clocks, kept = self.clocks([], {"v0.43.0": NOW - 3 * sync.DAY, "v0.1.0": 5})
        self.assertEqual((clocks["v0.43.0"], kept["v0.43.0"]), (NOW - 3 * sync.DAY, NOW - 3 * sync.DAY))
        self.assertEqual(clocks["v0.42.3"], NOW)
        self.assertEqual(sorted(kept), sorted(TAGS))

    def test_a_release_replaces_the_first_sighting_and_drops_it_from_the_record(self):
        clocks, kept = self.clocks([release("v0.43.0")], {"v0.43.0": NOW - 30 * sync.DAY})
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
        for first_seen in (["v0.43.0"], {"v0.43.0": "1"}, {"v0.43.0": 1.5}, {"v0.43.0": True}):
            with self.subTest(first_seen=first_seen), self.assertRaises(sync.SyncError):
                sync.lag_clocks(TAGS, [], first_seen, NOW)


class SettingsGuardTests(unittest.TestCase):
    SAFE = ({"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
            {"total_count": 0, "secrets": []})

    def test_read_only_token_and_no_repository_secret_is_safe(self):
        self.assertEqual(sync.settings_problems(*self.SAFE), [])

    def test_a_writable_default_token_is_unsafe(self):
        problems = sync.settings_problems({"default_workflow_permissions": "write"}, self.SAFE[1])
        self.assertEqual(problems, ["default workflow permissions are 'write', not 'read'"])

    def test_any_repository_secret_is_unsafe(self):
        listing = {"total_count": 2, "secrets": [{"name": "A"}, {"name": "B"}]}
        self.assertEqual(sync.settings_problems(self.SAFE[0], listing), ["repository secrets exist (2): A, B"])
        self.assertEqual(len(sync.settings_problems(self.SAFE[0], {"total_count": 3, "secrets": []})), 1)
        self.assertEqual(len(sync.settings_problems(self.SAFE[0], {"total_count": 0, "secrets": [{"name": "A"}]})), 1)

    def test_error_bodies_and_malformed_listings_are_unsafe(self):
        error = {"message": "Resource not accessible by personal access token", "status": "403"}
        self.assertEqual(len(sync.settings_problems(error, error)), 2)
        safe = self.SAFE[0]
        for permissions, secrets in ((safe, {"secrets": []}), (safe, {"total_count": "0", "secrets": []}),
                                     (safe, {"total_count": 0}), ([], self.SAFE[1]), (safe, [])):
            with self.subTest(permissions=permissions, secrets=secrets):
                self.assertTrue(sync.settings_problems(permissions, secrets))

    def run_guard(self, permissions, secrets):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "permissions.json", Path(tmp) / "secrets.json"]
            for path, content in zip(paths, (permissions, secrets), strict=True):
                path.write_text(content, encoding="utf-8")
            error = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(error):
                code = sync.main(["guard", "--workflow-permissions", str(paths[0]), "--secrets", str(paths[1])])
            return code, error.getvalue()

    def test_guard_command_passes_only_safe_settings(self):
        safe = [json.dumps(part) for part in self.SAFE]
        self.assertEqual(self.run_guard(*safe), (0, ""))
        code, error = self.run_guard('{"default_workflow_permissions": "write"}',
                                     '{"total_count": 1, "secrets": [{"name": "A"}]}')
        self.assertEqual(code, 2)
        self.assertIn("a sync push is unsafe: default workflow permissions are 'write', not 'read'; "
                      "repository secrets exist (1): A", error)
        self.assertEqual(self.run_guard("", safe[1])[0], 2)


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

    def test_every_body_says_to_bump_both_base_pin_files_to_the_contained_release(self):
        for source in ("umputun", "linux-port"):
            for conflicts in ((), [("a", "both modified")]):
                with self.subTest(source=source, conflicts=conflicts):
                    body = text(conflicts, source=source)[3]
                    self.assertIn("bump `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to `v0.43.0` / "
                                  f"`{'c' * 40}` in both `.github/workflows/ci.yml` and "
                                  "`.github/workflows/release-linux.yml`", body)

    def test_pins_never_move_back_when_main_holds_a_newer_release(self):
        for source in ("umputun", "linux-port"):
            with self.subTest(source=source):
                body = text(source=source, pin=("v0.44.0", "f" * 40))[3]
                self.assertIn(f"keep `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` at `v0.44.0` / `{'f' * 40}` in both "
                              "`.github/workflows/ci.yml` and `.github/workflows/release-linux.yml`", body)
                self.assertIn("never move them back to `v0.43.0`", body)
                self.assertNotIn("bump `UPSTREAM_BASE_TAG`", body)

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
        self.assertLess(upload, self.position("          overwrite: true"))
        self.position('select((.expired | not) and .workflow_run.head_branch == "main"\n'
                      "              and .workflow_run.head_repository_id == .workflow_run.repository_id)")

    def test_the_settings_guard_runs_as_the_pat_before_the_push(self):
        guard = self.position("python3 scripts/upstream-sync.py guard --workflow-permissions")
        self.assertLess(guard, self.position("run: scripts/upstream-sync-publish.sh"))
        self.assertLess(self.position("    environment: upstream-sync"), guard)
        token = self.position("          GH_TOKEN: ${{ secrets.UPSTREAM_SYNC_TOKEN }}\n")
        self.assertLess(self.position("name: Require the settings that make the push safe"), token)
        self.assertLess(token, guard)
        for listing in ("setting permissions/workflow workflow-permissions.json", "setting secrets secrets.json"):
            self.assertLess(token, self.position(listing))

    def test_labels_exist_before_the_publish_script_runs(self):
        self.assertLess(self.position("name: Create the sync labels if missing"),
                        self.position("run: scripts/upstream-sync-publish.sh"))
        self.assertNotIn("labels[]=", WORKFLOW)


STUB = r"""#!/usr/bin/env bash
printf '%s\t%s\t%s\n' "$(basename "$0")" "${GH_TOKEN:-}|${GIT_CONFIG_VALUE_0:-}" "$*" >> "$STUB_LOG"
if [[ "$(basename "$0")" == git ]]; then
  ref="refs/heads/$BRANCH"
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

    def test_missing_inputs_fail_before_any_call(self):
        result, calls, _ = self.run_publish(STUB_CREATE="ok", SYNC_TOKEN="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])


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

    def merge(self, *args):
        """(exit status, the outputs, the body) of the `merge` command against this repository."""
        body, out = self.root / "body.md", io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "out")}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = sync.main(["merge", "--base", "main", "--worktree", str(self.root / "wt"), "--body", str(body),
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
        self.assertEqual(json.loads(seen.read_text(encoding="utf-8")), {"v0.3.0": first})
        code, plan = self.plan("--now", str(first + LAG), *record)
        self.assertEqual(plan["source"], "")
        self.assertEqual(json.loads(seen.read_text(encoding="utf-8")), {"v0.3.0": first})
        code, plan = self.plan("--now", str(first + LAG + 1), *record)
        self.assertEqual((plan["source"], plan["tag"]), ("umputun", "v0.3.0"))
        self.assertIn("has lacked v0.3.0 for 7.0 days, more than 7", plan["reason"])

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
        seen.write_text('{"v0.3.0": "yesterday"}', encoding="utf-8")
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
        conflicts, github = sync.build(self.root / "wt", self.head(), "sync/umputun-v0.3.0", clean, "upstream v0.3.0")
        self.assertEqual((conflicts, github), ([], []))
        parents = self.git("rev-list", "--parents", "-n", "1", "sync/umputun-v0.3.0").split()[1:]
        self.assertEqual(parents, [self.head(), clean])
        self.assertEqual(self.git("log", "-1", "--format=%s", "sync/umputun-v0.3.0"), "Merge upstream v0.3.0 into main")

    def test_conflicts_are_committed_with_markers_and_both_parents(self):
        self.commit("shared.txt", "fork\n", "fork change")
        main = self.head()
        conflicts, _ = sync.build(self.root / "wt", main, "sync/umputun-v0.2.0", self.upstream, "upstream v0.2.0")
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
        _, github = sync.build(self.root / "wt", self.head(), "sync/umputun-v0.4.0", upstream, "upstream v0.4.0")
        self.assertEqual(github, [".github/workflows/upstream.yml"])

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
        self.assertIn(f"bump `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` to `v0.2.0` / `{self.upstream}`", body)

    def test_linux_port_merge_keeps_the_pins_at_a_newer_release_main_already_has(self):
        self.git("merge", "-q", "--no-edit", self.upstream)
        self.git("checkout", "-q", "-b", "old-port", self.base_tag)
        self.commit("port-only.txt", "port\n", "linux adaptation on the old release")
        port = self.head()
        self.git("checkout", "-q", "main")
        code, result, body = self.merge("--source", "linux-port", "--branch", f"sync/linux-port-{port[:8]}",
                                        "--commit", port)
        self.assertEqual(code, 0)
        self.assertIn("(umputun/agterm v0.1.0)", result["title"])
        self.assertIn(f"keep `UPSTREAM_BASE_TAG` / `UPSTREAM_BASE_COMMIT` at `v0.2.0` / `{self.upstream}`", body)
        self.assertIn("never move them back to `v0.1.0`", body)

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
