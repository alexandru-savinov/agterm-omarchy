#!/usr/bin/env python3
"""Read original records and source only; never execute original helpers."""
import sys
sys.dont_write_bytecode = True
import argparse
import ast
import collections
import copy
import hashlib
import json
from pathlib import Path
import re

SOURCE = "77cce43eb791ba1a983c19760c7df5259d0e452d"
TARGET = "editorCommandWorksUnderNonPosixLoginShell()"
TARGET_ID = "agtermCoreTests.ConfigPathsTests/" + TARGET
FILTER = r"^agtermCoreTests\.ConfigPathsTests/editorCommandWorksUnderNonPosixLoginShell\(\)(/|$)"
ORIGINAL_HASHES = {
    "native-fish-package-plan-private.json": "f48aef57c9167581f26c5dce78c54cc02410915a9b2e2a42b76f678f01775b43",
    "native-fish-preparation-launch-private.json": "d844096eeac90127c9e4bb8087c60a191b17f1712f228d79dd0b3d0c2de9608d",
    "native-fish-preparation-result-private.json": "581f7892932bc255d4452d870fe4f5275b67472ccbdbe4b3d4e6f6a975a69c21",
    "native-fish-help-launch-private.json": "82a1b5edad9b6bc7970fffe45d8400cd91df65f939119a7c750009059468f8f9",
    "native-fish-help-result-private.json": "2e5f5a1e91637cf2538392f6b1018b12e0e7a1a312c24d446c6567e4a28216b3",
    "native-fish-help-final-private.json": "a47de2509ec0961580624f1d6a5954d78f8488c9f097d1e86474dbd1a39092e6",
    "native-fish-help-administrative-stop-private.json": "015c74140224ba37dfdcb77761edfedc2de04e9cd7ae363f26cef324b95ba581",
    "native-fish-list-launch-private.json": "f6243166a0b621eb41c0c5a575c6ba1ed7bdf63ae1b159dd8da108f6c775e507",
    "native-fish-list-result-private.json": "64786fd610a42d66ccbada90782de0f755a329fde5c51ae773b420be1b91192f",
    "native-fish-fixture-launch-private.json": "d0936e5bcadbf93d926d6251b7657bebbebb09ca9083976c3469af3e7468fb61",
    "native-fish-fixture-result-private.json": "69ccb9bc62fa2836176771d049073bf5c3f2395eae7a298dd04c26a242dcbb2a",
    "native-fish-fixture-post-verification.json": "3f81c9ab7a2e06e6e2496322a88d41a06f9f52e60a08c7c27938cafc90145856",
    "prepare-native-fish.py": "ba319a6edf2f64c46012a6a0391e2c242112b87b5d3511e5ff2321908b3dc31c",
    "run-native-fish-fixture.py": "5ae34ac9c969d8ece1ff8298fbdc821b9741c3f779a2d81e74e1311b261bf303",
    "run-native-fish-fixture-v2.py": "6324c88858b9ad3ea27defc462bdb3696496631920b78273b1cbdbfa416d79cf",
    "assess-mac-private-contexts.py": "a51fbeea4954a23631c32be6a2f44273f5af6ad0b704a2d3c8e3725b5789e107",
    "scanner-zmx-context-diagnostic-sf79c3480ffb/manifest.json": "bb299e7b294f20a28a7ab4196aff5ecb4e1c730552c7bb8bb490233bf00348f0",
    "mac-private-policy-assessment-01/pristine-bundle-stderr.log": "781edc4dca4980fc3c204e7008997e4aadd3c7f3c096b5cdce62e239abf5bc77",
    "mac-private-policy-assessment-01/pristine-bundle-stdout.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "mac-private-policy-assessment-01/pristine-image-stderr.log": "f5bdab13416e78622cf17c3a0e86105ce14a45a56bffc0c4941aa1a50c359769",
    "mac-private-policy-assessment-01/pristine-image-stdout.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "mac-private-policy-assessment-01/result.json": "fcdb14d9a15412110af6e24663bdf01bffdb0327d2433cdc40ceacc6134c5196",
    "mac-private-policy-assessment-01/standalone-image-stderr.log": "22328af17fd8549f9343d0b448ee7ababd32981db613e701e553a6c183643ca0",
    "mac-private-policy-assessment-01/standalone-image-stdout.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "mac-private-policy-assessment-01/with-peer-bundle-stderr.log": "e3c43696da9591fa99fbcee664c1b8440522bdcb15573799fdebe517e2654b61",
    "mac-private-policy-assessment-01/with-peer-bundle-stdout.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "mac-private-policy-assessment-01/with-peer-image-stderr.log": "5ccbfe724bd2ecc37a9ea71006e8245d23c02a9606d41172180eed08c5d91ef6",
    "mac-private-policy-assessment-01/with-peer-image-stdout.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
CONTEXT = "scanner-zmx-context-diagnostic-sf79c3480ffb"
POLICY = "mac-private-policy-assessment-01"
FIXTURE_SOURCE = "agtermCore/Tests/agtermCoreTests/ConfigPathsTests.swift"
NATIVE_CHECKS = ("source_unchanged", "installed_unchanged", "payload_unchanged",
                 "private_package_unchanged", "protected_preserved",
                 "login_shell_unchanged", "global_fish_absent",
                 "usr_local_unchanged", "unit_cgroup_absent")
MAC_CHECKS = ("installed_tree_unchanged", "installed_attributes_unchanged",
              "originals_unchanged", "prepared_package_unchanged",
              "protected_preserved")
DOCS = ("native-fish.json", "mac-policy.json", "log-projections.json",
        "original-bindings.json")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def binding(data):
    return {"bytes": len(data), "sha256": sha(data)}


def read_file(root, relative):
    path = root / relative
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts
            and path.is_file() and path.resolve().is_relative_to(root.resolve()),
            "unsafe or missing input")
    return path.read_bytes()


def unpack(data):
    return json.loads(json.loads(data)["stdout"])


def load_originals(root):
    result = {}
    for name, expected in ORIGINAL_HASHES.items():
        data = read_file(root, name)
        require(sha(data) == expected, "original changed: " + name)
        result[name] = data
    return result


def verify_context_inputs(root, originals):
    manifest = json.loads(originals[CONTEXT + "/manifest.json"])
    count = total = 0
    for key, prefix in (("files", CONTEXT + "/"), ("preserved_originals", "")):
        for row in manifest[key]:
            data = read_file(root, prefix + row["path"])
            require(binding(data) == {"bytes": row["size"], "sha256": row["sha256"]},
                    "retained Mac context input changed")
            count += 1
            total += len(data)
    images = {}
    for name, case in manifest["cases"].items():
        path = CONTEXT + "/" + case["input"]
        if case["image_relative"] != ".":
            path += "/" + case["image_relative"]
        data = read_file(root, path)
        require(sha(data) == case["zmx_sha256"], "Mac context image changed")
        images[name] = sha(data)
    return {"manifest_entries_rehashed": count, "entry_bytes_rehashed": total,
            "context_image_sha256": images}


def log_text(value):
    return value if isinstance(value, str) else value["text"]


def literal_recount(text, status):
    require(status["exit_code"] == 0, "fixture service did not succeed")
    require(sha(text.encode()) == status["test_log_sha256"], "fixture log changed")
    starts = re.findall(r"^◇ Test (?!run started\.)([^\r\n]+) started\.$", text, re.M)
    passes = re.findall(r"^✔ Test (?!run with )([^\r\n]+) passed after ([0-9.]+) seconds\.$",
                        text, re.M)
    failed = re.findall(r"^✘ Test [^\r\n]+ failed", text, re.M)
    skipped = re.findall(r"^✘ Test [^\r\n]+ skipped", text, re.M)
    suites = re.findall(r"^◇ Suite ([^\r\n]+) started\.$", text, re.M)
    summaries = re.findall(r"^✔ Test run with (\d+) test in (\d+) suite passed after ([0-9.]+) seconds\.$",
                           text, re.M)
    require(starts == [TARGET] and len(passes) == 1 and passes[0][0] == TARGET
            and not failed and not skipped and suites == ["ConfigPathsTests"]
            and summaries == [("1", "1", passes[0][1])],
            "expected one literal test start/pass and one complete summary")
    require(all(status[k] is True for k in NATIVE_CHECKS)
            and status["fixture_remainders"] == [], "fixture preservation/cleanup failed")
    return {"test_starts": 1, "test_passes": 1, "test_failures": 0,
            "test_skips": 0, "suite_count": 1, "test_seconds": float(passes[0][1])}


def source_bounds(data):
    tree = ast.parse(data)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    timeouts = sorted(k.value.value for n in calls for k in n.keywords
                      if k.arg == "timeout" and isinstance(k.value, ast.Constant))
    require(timeouts == [10, 30, 115], "native outer timeout bounds changed")
    workers = [n for n in calls if isinstance(n.func, ast.Attribute)
               and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "worker" and n.func.attr == "write_text"]
    require(len(workers) == 1, "worker source missing")
    worker = ast.literal_eval(workers[0].args[0])
    deadlines = [n.args[-1].value for n in ast.walk(ast.parse(worker))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "command" and isinstance(n.args[-1], ast.Constant)]
    require(sorted(deadlines) == [10, 45], "native child timeout bounds changed")
    return {"core_child_seconds": 45, "fish_version_child_seconds": 10,
            "operator_service_wait_seconds": 115, "service_runtime_max_seconds": 90,
            "service_stop_seconds": 5}


def make_documents(originals, fixture_source, context_verification):
    for name, expected in ORIGINAL_HASHES.items():
        require(sha(originals[name]) == expected, "original byte binding changed")
    records = {name: unpack(data) for name, data in originals.items()
               if name.startswith("native-fish-") and name.endswith(".json")
               and name not in ("native-fish-preparation-launch-private.json",
                                "native-fish-fixture-post-verification.json")}
    prep_launch = json.loads(originals["native-fish-preparation-launch-private.json"])
    prep_identity = json.loads(prep_launch["transport"]["stdout"])
    package = records["native-fish-preparation-result-private.json"]["package_status"]
    plan = records["native-fish-package-plan-private.json"]
    post = json.loads(originals["native-fish-fixture-post-verification.json"])
    runs = {
        "help": records["native-fish-help-final-private.json"],
        "list": records["native-fish-list-result-private.json"],
        "fixture": records["native-fish-fixture-result-private.json"]}
    launch_names = {"help": "native-fish-help-launch-private.json",
                    "list": "native-fish-list-launch-private.json",
                    "fixture": "native-fish-fixture-launch-private.json"}
    result_names = {"help": "native-fish-help-final-private.json",
                    "list": "native-fish-list-result-private.json",
                    "fixture": "native-fish-fixture-result-private.json"}
    statuses = {mode: row["status"] for mode, row in runs.items()}
    reference = statuses["fixture"]
    require(reference["source"] == SOURCE, "source revision mismatch")
    require(sha(fixture_source) == reference["fixture_sha256"],
            "source77 fixture bytes changed")
    require(b"func editorCommandWorksUnderNonPosixLoginShell() throws" in fixture_source,
            "target source function missing")
    require(sha(originals["prepare-native-fish.py"]) == prep_launch["host_sha256"],
            "preparation host driver changed")
    require(sha(prep_launch["worker_source"].encode()) == prep_identity["worker_sha256"],
            "preparation worker bytes changed")
    preparation_ast = ast.parse(originals["prepare-native-fish.py"])
    embedded_workers = [ast.literal_eval(node.value) for node in preparation_ast.body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "worker"
                                for t in node.targets)]
    require(embedded_workers == [prep_launch["worker_source"]],
            "retained preparation worker differs from its host driver")
    preparation_timeouts = sorted(k.value.value
        for node in ast.walk(ast.parse(prep_launch["worker_source"]))
        if isinstance(node, ast.Call) for k in node.keywords
        if k.arg == "timeout" and isinstance(k.value, ast.Constant))
    require(preparation_timeouts == [15, 15, 50],
            "dependency preparation subprocess bounds changed")
    require(package["passed"] is True and package["state"] == "complete"
            and package["errors"] == [] and package["executed_package"] is False
            and package["source_keyring_unchanged"] is True,
            "dependency preparation failed")
    require(all(package["package"][k] == plan["package"][k]
                for k in package["package"]), "dependency plan changed")
    require(package["signature_verification"]["exit_code"] == 0
            and package["keyring_conversion"]["exit_code"] == 0
            and all(row["exit_code"] == 0 for row in package["downloads"]),
            "dependency download/signature verification failed")
    fingerprints = re.findall(r"^\[GNUPG:\] VALIDSIG ([A-F0-9]{40}) ",
                              package["signature_verification"]["stdout"], re.M)
    require(len(fingerprints) == 1, "valid signature receipt missing")
    require(package["fish_sha256"] == reference["fish_sha256"]
            == package["extracted_files"]["usr/bin/fish"]["sha256"],
            "dependency image receipt mismatch")
    native_bounds = {
        mode: source_bounds(originals["run-native-fish-fixture.py" if mode == "help"
                                     else "run-native-fish-fixture-v2.py"])
        for mode in runs}
    aliases = {prep_identity["stage"]: "<PRIVATE_FISH_STAGE>"}
    for mode, result in runs.items():
        status = result["status"]
        launch = records[launch_names[mode]]
        settings = json.loads(status["argv"][-1])
        aliases[launch["stage"]] = "<" + mode.upper() + "_STAGE>"
        aliases[status["unit"]] = "<" + mode.upper() + "_UNIT>"
        aliases[settings["source"]] = "<SOURCE77>"
        for arg in status["argv"]:
            if arg.startswith("LD_LIBRARY_PATH="):
                aliases[arg.split("=", 1)[1]] = "<SWIFT_COMPAT>"
        expected_driver = originals["run-native-fish-fixture.py" if mode == "help"
                                    else "run-native-fish-fixture-v2.py"]
        require(status["driver_sha256"] == launch["driver_sha256"] == sha(expected_driver),
                "native driver binding mismatch")
        require(all(status[k] == reference[k] for k in
                    ("source", "binary_sha256", "fish_sha256", "fixture_sha256")),
                "native source/image binding changed across attempts")
        require(all(status[k] is True for k in NATIVE_CHECKS),
                "native historical preservation check failed")
        worker = status["worker"]
        require(worker["uid"] > 0 and worker["gid"] > 0
                and all(worker[k] is True for k in
                        ("source_readonly", "fish_mount_readonly", "installed_readonly")),
                "native read-only worker observation missing")
        require(all(worker["namespaces"][k] != settings["outer_ns"][k]
                    for k in ("mnt", "pid", "net", "ipc")),
                "native private namespace comparison failed")
        properties = {arg.removeprefix("--property=").split("=", 1)[0]:
                      arg.removeprefix("--property=").split("=", 1)[1]
                      for arg in status["argv"] if arg.startswith("--property=")}
        require(properties["RuntimeMaxSec"] == "90"
                and properties["TimeoutStopSec"] == "5"
                and properties["KillMode"] == "control-group"
                and properties["PrivatePIDs"] == "yes"
                and properties["BindReadOnlyPaths"] ==
                    prep_identity["stage"] + "/prefix/usr:/usr/local",
                "native service/mount bounds changed")
        require(status["unit_after"] == {
            "exit_code": 0, "stdout":
            "LoadState=not-found\nActiveState=inactive\nMainPID=0\nControlGroup=\n",
            "stderr": ""}, "native unit not collected")
    mac = json.loads(originals[POLICY + "/result.json"])
    aliases[mac["stage"]] = "<MAC_ASSESSMENT_STAGE>"

    def sanitize(text):
        for private, alias in sorted(aliases.items(), key=lambda x: len(x[0]), reverse=True):
            text = text.replace(private, alias)
        return text

    projections = {}

    def project(key, origin, location, text, raw=None, indices=None):
        lines = text.splitlines(keepends=True)
        selected = range(len(lines)) if indices is None else sorted(set(indices))
        output = []
        for i in selected:
            transformed = sanitize(lines[i])
            reverse = transformed
            for private, alias in aliases.items():
                reverse = reverse.replace(alias, private)
            require(reverse == lines[i], "selected log alias cannot be reversed")
            output.append({"line": i + 1, "text": transformed})
        captured = binding(text.encode())
        matches = None if raw is None else captured == raw
        projections[key] = {
            "original_record": origin, "extraction": location,
            "recorded_raw_receipt": raw, "retained_decoded_utf8": captured,
            "decoded_capture_matches_raw_receipt": matches,
            "coverage": "complete" if indices is None else "explicit selected lines",
            "original_line_count": len(lines), "lines": output}
        return matches

    native = {}
    for mode, result in runs.items():
        status = result["status"]
        native_logs = {}
        for name, row in result["logs"].items():
            text = row["text"]
            raw = {"sha256": row["sha256"], "bytes": row["bytes"]}
            indices = None
            if name == "work/core-help.log":
                lines = text.splitlines(keepends=True)
                starts = [i for i, line in enumerate(lines)
                          if re.match(r"^◇ Test [A-Za-z_]\w*\(\) started\.", line)]
                passes = [i for i, line in enumerate(lines)
                          if re.match(r"^✔ Test [A-Za-z_]\w*\(\) passed after ", line)]
                indices = list(range(min(3, len(lines)))) + starts[:2] + passes[-2:]
            elif name == "work/core-list.log":
                lines = text.splitlines(keepends=True)
                indices = [i for i, line in enumerate(lines)
                           if line.rstrip("\r\n") == TARGET_ID]
            key = "native-" + mode + "/" + name
            matches = project(key, result_names[mode], "JSON(stdout).logs[" + name + "].text",
                              text, raw, indices)
            if mode == "help" and name == "work/core-help.log":
                require(matches is False and raw["sha256"] == status["test_log_sha256"],
                        "expected explicit earlier decoded/raw log gap")
            else:
                require(matches is True, "native log raw byte binding mismatch")
            native_logs[name] = key
        core = result["logs"]["work/core-" + mode + ".log"]["text"]
        require(result["logs"]["service.log"]["sha256"] == status["service_log_sha256"],
                "service log receipt mismatch")
        require(result["logs"]["work/core-" + mode + ".log"]["sha256"]
                == status["test_log_sha256"], "core log receipt mismatch")
        worker = status["worker"]
        settings = json.loads(status["argv"][-1])
        commands = []
        for command in worker["commands"]:
            row = copy.deepcopy(command)
            row["argv"] = [sanitize(v) for v in row["argv"]]
            commands.append(row)
            if "log_sha256" in row:
                require(row["log_sha256"] ==
                        result["logs"]["work/" + row["label"] + ".log"]["sha256"],
                        "worker command log receipt mismatch")
        remainder_categories = collections.Counter(re.sub(
            r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}",
            "<fixture-id>", value) for value in status["fixture_remainders"])
        native[mode] = {
            "original_aggregate_passed": status["passed"],
            "original_test_selection_verified": status["test_selection_verified"],
            "service_exit_code": status["exit_code"],
            "service_seconds": status["seconds"], "driver_sha256": status["driver_sha256"],
            "bounds": native_bounds[mode], "errors": status["errors"],
            "worker_completed": worker.get("completed", False),
            "worker_commands": commands,
            "worker_readonly": {k: worker[k] for k in
                               ("source_readonly", "fish_mount_readonly", "installed_readonly")},
            "worker_non_root": worker["uid"] > 0 and worker["gid"] > 0,
            "private_namespace_comparisons": {
                k: worker["namespaces"][k] != settings["outer_ns"][k]
                for k in ("mnt", "pid", "net", "ipc")},
            "preservation": {k: status[k] for k in NATIVE_CHECKS},
            "unit_after": status["unit_after"],
            "fixture_remainders": {
                "count": len(status["fixture_remainders"]),
                "categories": dict(remainder_categories),
                "canonical_list_binding": binding(json.dumps(
                    status["fixture_remainders"], separators=(",", ":")).encode()),
                "scope": "Selected count/categories; exact private names remain in the bound original record."},
            "recorded_partial_literal_pass_count": len(status["literal_passes"]),
            "recorded_literal_skip_count": len(status["literal_skips"]),
            "log_projections": native_logs}
        if mode == "help":
            require(status["passed"] is False and status["exit_code"] == 1
                    and status["test_selection_verified"] is False
                    and not worker.get("completed")
                    and len(status["fixture_remainders"]) > 0,
                    "accidental run must remain failed and incomplete")
            require(worker["commands"][-1]["argv"][-1] == "--help"
                    and "exit_code" not in worker["commands"][-1]
                    and "timed out after 45 seconds" in result["logs"]["service.log"]["text"],
                    "accidental invocation/deadline evidence missing")
            native[mode]["outcome"] = "failed_incomplete_accidental_full_selection"
            native[mode]["raw_log_byte_identity_established_from_capture"] = False
        elif mode == "list":
            identifiers = core.splitlines()
            require(status["passed"] is True and status["exit_code"] == 0
                    and worker.get("completed") and status["fixture_remainders"] == []
                    and worker["commands"][-1]["argv"][1:] ==
                        ["--testing-library", "swift-testing", "--list-tests"],
                    "listing invocation failed")
            require(len(identifiers) == 4105 and identifiers.count(TARGET_ID) == 1
                    and not re.search(r"^[◇✔✘] (?:Test|Suite) ", core, re.M),
                    "listing is not test-body-free identifier output")
            native[mode].update(outcome="listing_only", identifier_count=len(identifiers),
                                requested_identifier_count=1)
        else:
            require(worker["commands"][-1]["argv"][1:] ==
                    ["--testing-library", "swift-testing", "--no-parallel",
                     "--repetitions", "1", "--filter", FILTER],
                    "single-test argument vector changed")
            recount = literal_recount(core, status)
            require(status["passed"] is False and status["test_selection_verified"] is False
                    and status["literal_passes"] == [TARGET, "run with 1 test in 1 suite"],
                    "original counting defect was erased")
            native[mode].update(outcome="literal_single_test_pass_with_failed_original_aggregate",
                                original_literal_passes=status["literal_passes"],
                                independent_file_only_recount=recount)

    interim_name = "native-fish-help-result-private.json"
    interim = records[interim_name]
    require("exit_code" not in interim["status"]
            and "fixture_remainders" not in interim["status"], "interim receipt was made final")
    interim_text = interim["logs"]["work/core-help.log"]
    project("native-help-interim/core-log", interim_name,
            "JSON(stdout).logs[work/core-help.log]", interim_text,
            indices=list(range(min(3, len(interim_text.splitlines())))))
    require(post["original_record_sha256"] ==
            sha(originals["native-fish-fixture-result-private.json"])
            and post["test_log_sha256"] == reference["test_log_sha256"]
            and post["binary_sha256"] == reference["binary_sha256"]
            and post["fish_sha256"] == reference["fish_sha256"]
            and post["source"] == SOURCE and post["post_run_verification_passed"] is True
            and post["runtime_repeated"] is False
            and post["original_aggregate_passed"] is False
            and post["original_test_selection_verified"] is False
            and all(post["negative_controls"].values()), "retained recount receipt mismatch")
    require((post["literal_test_passes"], post["literal_test_failures"],
             post["literal_test_skips"], post["suite_count"]) == (1, 0, 0, 1),
            "retained recount cardinality changed")
    stop = records["native-fish-help-administrative-stop-private.json"]
    require(stop["unit"] == statuses["help"]["unit"]
            and stop["already_inactive"] is True and stop["cgroup_absent"] is True,
            "later stop check does not refer to the inactive failed service")
    fish_report = {
        "schema": "agterm-fish-additive-evidence/1", "source": SOURCE,
        "native_test_binary_sha256": reference["binary_sha256"],
        "source_fixture": binding(fixture_source) | {
            "git_blob": hashlib.sha1(b"blob " + str(len(fixture_source)).encode()
                                     + b"\0" + fixture_source).hexdigest()},
        "image_binding_scope": "Native image/package digests are historical receipts cross-checked across attempts; this verifier does not access the guest binaries.",
        "dependency_preparation": {
            "package": package["package"],
            "host_driver_sha256": prep_launch["host_sha256"],
            "worker_source_sha256": prep_identity["worker_sha256"],
            "subprocess_bounds_seconds": {
                "keyring_conversion": 15, "each_download": 50,
                "signature_verification": 15},
            "passed": package["passed"], "executed_package_during_preparation": False,
            "signature_sha256": package["signature_sha256"],
            "public_keyring_sha256": package["public_keyring_sha256"],
            "source_public_keyring_sha256": package["source_public_keyring_sha256"],
            "source_keyring_unchanged": package["source_keyring_unchanged"],
            "valid_signature_fingerprints": fingerprints,
            "signature_verification_exit_code": package["signature_verification"]["exit_code"],
            "archive_members": package["archive_members"],
            "extracted_file_records": len(package["extracted_files"]),
            "fish_sha256": package["fish_sha256"]},
        "attempts": native,
        "interim_help_receipt": {"final_outcome_available": False,
            "record_binding": binding(originals[interim_name]),
            "log_projection": "native-help-interim/core-log"},
        "later_stop_check": {
            "already_inactive": stop["already_inactive"],
            "cgroup_absent": stop["cgroup_absent"], "before": stop["before"],
            "scope": "The later administrative check found the service inactive; root reported sending no signal."},
        "retained_read_only_recount": post,
        "record_scope": "Selected original fields and explicit derivations; private identity values are omitted.",
        "limits": [
            "The accidental help invocation remains failed/incomplete with nonempty recorded temporary remainders.",
            "The original selected-run aggregate remains false; the literal test pass and later read-only recount are separate evidence.",
            "The accidental core-log decoded text is not byte-identical to its recorded raw receipt; both identities are retained distinctly.",
            "No runtime was repeated by the recount or this publication preparation.",
            "One supplementary test does not relabel the earlier full-core run or certify a newer candidate."]}

    manifest_data = originals[CONTEXT + "/manifest.json"]
    require(mac["source_diagnostic_manifest_sha256"] == sha(manifest_data)
            and mac["driver_sha256"] == sha(originals["assess-mac-private-contexts.py"]),
            "Mac assessment driver/input binding mismatch")
    assessment_ast = ast.parse(originals["assess-mac-private-contexts.py"])
    assessment_timeouts = [k.value.value for node in ast.walk(assessment_ast)
                           if isinstance(node, ast.Call) for k in node.keywords
                           if k.arg == "timeout" and isinstance(k.value, ast.Constant)]
    require(assessment_timeouts == [30], "Mac assessment source timeout changed")
    require(mac["assessment_only"] is True and mac["administrative_stop_acceptance"] is False
            and mac["complete"] is True and mac["errors"] == []
            and all(mac[k] is True for k in MAC_CHECKS)
            and all(all(row.values()) for row in mac["contexts"].values()),
            "Mac historical assessment/preservation outcome changed")
    expected_cases = [("standalone", "image", 3), ("pristine", "bundle", 0),
                      ("pristine", "image", 3), ("with-peer", "bundle", 1),
                      ("with-peer", "image", 3)]
    require([(v["context"], v["target_kind"], v["exit_code"])
             for v in mac["assessments"]] == expected_cases, "Mac assessment results changed")
    assessments = []
    for row in mac["assessments"]:
        require(row["timeout_seconds"] == 30 and 0 <= row["seconds"] < 30
                and not row.get("timed_out", False)
                and row["argv"][:5] == ["/usr/sbin/spctl", "--assess", "--type", "execute", "-vv"],
                "Mac assessment bounds/command changed")
        item = {k: row[k] for k in
                ("context", "target_kind", "exit_code", "seconds", "timeout_seconds")}
        item["argv"] = [sanitize(v) for v in row["argv"]]
        item["log_projections"] = {}
        for stream in ("stdout", "stderr"):
            name = row["context"] + "-" + row["target_kind"] + "-" + stream + ".log"
            data = originals[POLICY + "/" + name]
            require(data.decode() == row[stream] and sha(data) == row[stream + "_sha256"],
                    "Mac raw assessment log differs from record")
            lines = row[stream].splitlines(keepends=True)
            selected = [i for i, line in enumerate(lines) if not line.startswith("origin=")]
            key = "mac/" + name
            project(key, POLICY + "/" + name, "complete raw log file",
                    row[stream], binding(data), None if len(selected) == len(lines) else selected)
            item["log_projections"][stream] = key
        assessments.append(item)
    mac_report = {
        "schema": "agterm-mac-policy-assessment/1",
        "assessment_only": True, "administrative_stop_acceptance": False,
        "launch_delay_causation_established": False,
        "driver_sha256": mac["driver_sha256"],
        "source_diagnostic_manifest_sha256": mac["source_diagnostic_manifest_sha256"],
        "assessments": assessments, "contexts": mac["contexts"],
        "preservation": {k: mac[k] for k in MAC_CHECKS},
        "protected_process_count_recorded": len(mac["protected_before"]),
        "errors": mac["errors"], "complete": mac["complete"],
        "retained_input_verification": context_verification,
        "limits": [
            "These commands assess signatures/policy; they do not execute the target helper or reach an administrative-stop request.",
            "The pristine bundle is accepted; the disclosed peer addition invalidates the augmented aggregate resource seal.",
            "All three individual images receive the same not-an-app result, including the previously successful standalone context.",
            "No launch-delay cause, stopped reply, or release acceptance follows.",
            "Context/installed/protected preservation fields are historical observations; retained assessment stages are not claimed deleted."]}
    bindings = {
        "schema": "agterm-fish-policy-bindings/1",
        "original_records_drivers_and_logs": {k: binding(v) for k, v in sorted(originals.items())},
        "source_fixture": {"source": SOURCE, "path": FIXTURE_SOURCE,
                           **binding(fixture_source)},
        "transitive_mac_context_inputs": {
            "manifest": CONTEXT + "/manifest.json", **context_verification},
        "privacy": "Complete original bytes remain private. Published record fields are selected; log line selections and text/raw byte differences are explicit."}
    documents = dict(zip(DOCS, (fish_report, mac_report,
                               {"schema": "agterm-fish-policy-log-projections/1",
                                "streams": projections}, bindings)))
    rendered = json.dumps(documents)
    for private in aliases:
        require(private not in rendered, "private path/unit survived")
    for pattern in (r"/(?:home|Users)/[A-Za-z0-9_.-]+/",
                    r"/private/tmp/[A-Za-z0-9_.-]+",
                    r"agterm-fish-test-[0-9a-f]+\.service",
                    r"(?:mnt|pid|net|ipc):\[\d+\]"):
        require(not re.search(pattern, rendered), "private value survived projection")
    return documents


def validate_candidate(candidate, expected):
    require(candidate == expected, "proposed extract differs from bound originals")


def negatives(expected, originals):
    fixture = unpack(originals["native-fish-fixture-result-private.json"])
    text = fixture["logs"]["work/core-fixture.log"]["text"]
    status = fixture["status"]
    controls = [
        (text.replace("✔ Test " + TARGET, "REMOVED " + TARGET), status),
        (text + "◇ Test unexpected() started.\n", status),
        ("\n".join(v for v in text.split("\n") if not v.startswith("✔ Test run with ")), status),
        (text, dict(status, exit_code=1)),
        (text, dict(status, test_log_sha256="0" * 64))]
    count = 0
    for modified, state in controls:
        # Bind modified text for semantic log controls, retaining the deliberate
        # wrong-input hash only in the final control.
        state = dict(state)
        if modified != text:
            state["test_log_sha256"] = sha(modified.encode())
        try:
            literal_recount(modified, state)
        except ValueError:
            count += 1
        else:
            raise ValueError("negative recount control accepted")
    changes = (
        lambda c: c[DOCS[0]]["attempts"]["help"].update(original_aggregate_passed=True),
        lambda c: c[DOCS[0]]["attempts"]["help"]["fixture_remainders"].update(count=0),
        lambda c: c[DOCS[0]]["attempts"]["fixture"].update(original_aggregate_passed=True),
        lambda c: c[DOCS[1]].update(administrative_stop_acceptance=True),
        lambda c: c[DOCS[1]]["assessments"][0].update(timeout_seconds=300))
    for change in changes:
        candidate = copy.deepcopy(expected)
        change(candidate)
        try:
            validate_candidate(candidate, expected)
        except ValueError:
            count += 1
        else:
            raise ValueError("negative outcome control accepted")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads(read_file(package, "manifest.json"))
    require({p.name for p in package.iterdir()} ==
            set(manifest["files"]) | {"manifest.json"}, "public file set changed")
    for name, expected in manifest["files"].items():
        require(binding(read_file(package, name)) == expected, "public file binding changed")
    originals = load_originals(args.original_root)
    fixture_source = read_file(args.source_root, FIXTURE_SOURCE)
    contexts = verify_context_inputs(args.original_root, originals)
    expected = make_documents(originals, fixture_source, contexts)
    candidate = {name: json.loads(read_file(package, name)) for name in DOCS}
    validate_candidate(candidate, expected)
    controls = negatives(expected, originals)
    print(json.dumps({
        "passed": True, "original_files_rehashed": len(originals),
        "retained_mac_context_input_entries_rehashed": contexts["manifest_entries_rehashed"],
        "source_fixture_rehashed": True, "listing_identifiers": 4105,
        "single_test_passes": 1, "mac_assessments": 5,
        "negative_controls_rejected": controls,
        "failed_help_decoded_raw_byte_gap_preserved": True,
        "failed_help_temporary_remainders_preserved": True,
        "original_false_fixture_aggregate_preserved": True,
        "runtime_compiler_helper_VM_operations": 0,
        "same_account_not_independent_approval": True}, indent=2))


if __name__ == "__main__":
    main()
