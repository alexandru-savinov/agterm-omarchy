#!/usr/bin/env python3
"""Read retained files and ASTs only; never import or execute original helpers."""
import sys

sys.dont_write_bytecode = True

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re

SOURCE = "77cce43eb791ba1a983c19760c7df5259d0e452d"
ORIGINAL_HASHES = {
    "native-installer-fixtures-launch-private.json": "786fa779c5022b104badbf58318d6aaacaaecefa4ca5a05b1a9a73acbf3b486a",
    "native-installer-fixtures-result-private.json": "e18cc24a985a513e9217a54796bed70269a461ffb36683617c8fa566b826c83e",
    "native-installer-fixtures-attempt2-launch-private.json": "a7363940ce14608a6384be3679519635ad5aee3677d6b10bd0f0c80573d7a73d",
    "native-installer-fixtures-attempt2-result-private.json": "e6f32050bc2fd79ab794109a5cbc67a8fcc47152f63be6168502de9b211d19f9",
    "run-native-installer-fixtures.py": "081f7c99adfe8640923bd3d4a3a4bb4ea0530317a312bf86ca2488427122f363"
}
FIRST_LAUNCH = "native-installer-fixtures-launch-private.json"
FIRST_RESULT = "native-installer-fixtures-result-private.json"
PASS_LAUNCH = "native-installer-fixtures-attempt2-launch-private.json"
PASS_RESULT = "native-installer-fixtures-attempt2-result-private.json"
DRIVER = "run-native-installer-fixtures.py"
TEST_FILES = ("scripts/tests/test_linux_install.py",
              "scripts/tests/test_linux_install_safety.py")
DOCUMENTS = ("native-installer-fixtures.json", "original-bindings.json",
             "log-projections.json")
PRESERVATION = ("source_unchanged", "installed_unchanged", "payload_unchanged",
                "protected_preserved", "login_shell_unchanged")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def receipt(data):
    return {"bytes": len(data), "sha256": digest(data)}


def decode_record(data):
    outer = json.loads(data)
    return outer, json.loads(outer["stdout"])


def read_regular(root, relative):
    path = root / relative
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
            "unsafe relative input name")
    require(path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve()),
            "input must be a regular file within the supplied root")
    return path.read_bytes()


def read_originals(root):
    result = {}
    for name, expected in ORIGINAL_HASHES.items():
        data = read_regular(root, name)
        require(digest(data) == expected, "original byte binding changed: " + name)
        result[name] = data
    return result


def source_inputs(originals, root):
    _, retained = decode_record(originals[PASS_RESULT])
    return {name: read_regular(root, name)
            for name in retained["status"]["source_inputs"]}


def collection_receipt(outer):
    return {key: outer[key] for key in
            ("exit_code", "signal_code", "seconds")}


def make_documents(originals, sources):
    for name, expected in ORIGINAL_HASHES.items():
        require(digest(originals[name]) == expected,
                "original byte binding changed: " + name)
    launch1_outer, launch1 = decode_record(originals[FIRST_LAUNCH])
    result1_outer, result1 = decode_record(originals[FIRST_RESULT])
    launch2_outer, launch2 = decode_record(originals[PASS_LAUNCH])
    result2_outer, result2 = decode_record(originals[PASS_RESULT])
    status = result2["status"]
    driver = originals[DRIVER].decode("utf-8")
    driver_ast = ast.parse(driver)
    driver_hash = digest(originals[DRIVER])
    require(launch1["driver_sha256"] == launch2["driver_sha256"]
            == status["driver_sha256"] == driver_hash,
            "driver differs between prerequisite failure and completed attempt")
    require(status["source"] == SOURCE, "wrong source revision")
    require(status["passed"] is True and status["exit_code"] == 0
            and status["errors"] == [], "completed attempt is not a pass")
    require(result1["status"] is None and result1["log"] is None,
            "initial prerequisite failure incorrectly has a test result")
    failure = result1["operator_log"]
    require("operator.py" in failure and "from operator import eq as _eq" in failure
            and "ImportError:" in failure and "partially initialized" in failure,
            "initial module-name collision is missing")

    # Parse, do not execute, the operator's source-level bounds and worker.
    head_assignments = [n for n in driver_ast.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "head"
                                for t in n.targets)]
    require(len(head_assignments) == 1
            and ast.literal_eval(head_assignments[0].value) == SOURCE,
            "driver source pin mismatch")
    timeouts = [ast.literal_eval(k.value) for n in ast.walk(driver_ast)
                if isinstance(n, ast.Call) for k in n.keywords
                if k.arg == "timeout" and isinstance(k.value, ast.Constant)]
    require(sorted(timeouts) == [10, 30, 210], "operator timeout bounds changed")
    worker_calls = [n for n in ast.walk(driver_ast) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "worker" and n.func.attr == "write_text"]
    require(len(worker_calls) == 1, "worker source not found")
    worker_source = ast.literal_eval(worker_calls[0].args[0])
    worker_ast = ast.parse(worker_source)
    execute_calls = [n for n in ast.walk(worker_ast) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and isinstance(n.func.value, ast.Name)
                     and n.func.value.id == "os" and n.func.attr == "execv"]
    require(len(execute_calls) == 1, "unexpected worker dispatch")
    worker_argv = execute_calls[0].args[1].elts
    literals = [ast.literal_eval(v) for v in worker_argv
                if isinstance(v, ast.Constant)]
    require(literals == ["/usr/bin/python3", "-B", "-m", "unittest",
                         "discover", "-s", "-p", "test_linux_install*.py", "-v"],
            "fixture discovery command changed")

    source_records = {}
    definitions = {}
    require(set(sources) == set(status["source_inputs"]),
            "source input inventory changed")
    for name, expected in status["source_inputs"].items():
        data = sources[name]
        require(digest(data) == expected, "source content differs: " + name)
        git_blob = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        source_records[name] = receipt(data) | {"git_blob": git_blob}
    for name in TEST_FILES:
        tree = ast.parse(sources[name])
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_"):
                    test_id = Path(name).stem + "." + cls.name + "." + fn.name
                    require(test_id not in definitions, "duplicate test definition")
                    definitions[test_id] = {
                        "path": name, "class": cls.name,
                        "function": fn.name, "line": fn.lineno}
    log = result2["log"]
    require(digest(log.encode()) == status["log_sha256"],
            "retained full log hash mismatch")
    matches = re.findall(r"^(test_\w+) \(([^)]+)\) \.\.\. (.+)$", log, re.M)
    require(len(matches) == 46 and len({x[1] for x in matches}) == 46,
            "expected 46 distinct actual test results")
    require({x[1] for x in matches} == set(definitions),
            "logged tests do not match the two pinned source modules")
    require(all(name == ident.rsplit(".", 1)[1] and outcome == "ok"
                for name, ident, outcome in matches), "non-pass test result")
    summary = re.search(r"\nRan (\d+) tests in ([0-9.]+)s\n\nOK\n", log)
    require(summary is not None and int(summary[1]) == 46,
            "unittest summary changed")
    runtime = re.search(r"Service runtime: ([0-9.]+)s", log)
    require(runtime is not None
            and "Main processes terminated with: code=exited, status=0/SUCCESS" in log,
            "successful service completion missing")
    require(all(status[k] is True for k in PRESERVATION),
            "historical preservation check failed")
    require(status["unit_cgroup_absent"] is True
            and status["temporary_fixture_remainders"] == [],
            "historical cleanup check failed")
    require(status["unit_after"] == {
        "exit_code": 0, "stdout":
        "LoadState=not-found\nActiveState=inactive\nMainPID=0\nControlGroup=\n",
        "stderr": ""}, "service was not collected and inactive")
    worker = status["worker"]
    require(worker["uid"] > 0 and worker["gid"] > 0
            and worker["source_readonly"] is True
            and worker["installed_readonly"] is True,
            "non-root read-only worker observation missing")

    argv = status["argv"]
    require(argv[0] == "/usr/bin/systemd-run"
            and all(v in argv for v in ("--wait", "--pipe", "--collect",
                "--service-type=exec", "--expand-environment=no")),
            "service invocation semantics changed")
    properties = {v.removeprefix("--property=").split("=", 1)[0]:
                  v.removeprefix("--property=").split("=", 1)[1]
                  for v in argv if v.startswith("--property=")}
    fixed_properties = {
        "RuntimeMaxSec": "180", "TimeoutStopSec": "5",
        "KillMode": "control-group", "PrivateTmp": "yes",
        "PrivateNetwork": "yes", "PrivateIPC": "yes",
        "PrivateDevices": "yes", "ProtectSystem": "strict",
        "ProtectHome": "read-only", "NoNewPrivileges": "yes", "UMask": "0077"}
    require(all(properties.get(k) == v for k, v in fixed_properties.items())
            and set(properties) == set(fixed_properties) | {"ReadWritePaths"},
            "service bounds/isolation properties changed")
    source_path = next(v.split("=", 1)[1] for v in argv
                       if v.startswith("--working-directory="))
    require(properties["ReadWritePaths"] == launch2["stage"] + "/work",
            "unexpected writable scope")
    require("--uid=" + str(worker["uid"]) in argv
            and "--gid=" + str(worker["gid"]) in argv,
            "worker identity does not match service arguments")
    invocation = re.match(
        r"Running as unit: ([^;]+); invocation ID: ([0-9a-f]+)\n", log)
    require(invocation is not None and invocation[1] == status["unit"],
            "log service identity mismatch")
    replacements = {
        launch1["stage"]: "<FAILED_STAGE>",
        launch2["stage"]: "<FIXTURE_STAGE>",
        source_path: "<SOURCE77>",
        status["unit"]: "<TRANSIENT_UNIT>",
        invocation[2]: "<INVOCATION_ID>",
        "--uid=" + str(worker["uid"]): "--uid=<FIXTURE_UID>",
        "--gid=" + str(worker["gid"]): "--gid=<FIXTURE_GID>"}

    def sanitize(value):
        for private, alias in sorted(replacements.items(),
                                     key=lambda pair: len(pair[0]), reverse=True):
            value = value.replace(private, alias)
        return value

    streams = {}
    for key, origin, field, value in (
            ("prerequisite_traceback", FIRST_RESULT, "operator_log", failure),
            ("completed_tests", PASS_RESULT, "log", log),
            ("completed_operator", PASS_RESULT, "operator_log",
             result2["operator_log"])):
        transformed = sanitize(value)
        reversed_value = transformed
        for private, alias in replacements.items():
            reversed_value = reversed_value.replace(alias, private)
        require(reversed_value == value, "log aliases are not reversible")
        streams[key] = {
            "original_record": origin,
            "extraction": "JSON(record.stdout)." + field,
            "coverage": "complete decoded UTF-8 string; identity aliases only",
            "original_utf8": receipt(value.encode()),
            "line_count": len(value.splitlines()),
            "text": transformed}
    report = {
        "schema": "agterm-native-installer-fixtures/1",
        "scope": "Historical unchanged-source synthetic installer/staging/rollback fixtures; no graphical or release acceptance.",
        "record_scope": "Selected fields and derived comparisons; omitted process/unit/namespace/owner identities stay in the hash-bound originals.",
        "prerequisite_failure": {
            "outcome": "failed_before_fixture_execution",
            "cause": "operator.py shadows Python's standard operator module during imports",
            "status_record": None, "test_log": None,
            "completed_tests_observed": 0,
            "operator_exit_code": None,
            "operator_exit_code_scope": "Not separately retained; collection exit codes are not worker exit codes.",
            "launch_receipt_collection": collection_receipt(launch1_outer),
            "result_receipt_collection": collection_receipt(result1_outer),
            "driver_sha256": driver_hash},
        "completed_attempt": {
            "outcome": "passed", "source": SOURCE,
            "source_input_count": len(source_records),
            "driver_sha256": driver_hash,
            "scope": status["scope"], "errors": status["errors"],
            "fixture_exit_code": status["exit_code"],
            "test_count": len(matches), "unittest_seconds": float(summary[2]),
            "service_runtime_seconds": float(runtime[1]),
            "service_invocation_seconds": status["seconds"],
            "operator_wait_timeout_seconds": 210,
            "service_properties": fixed_properties | {"ReadWritePaths": "<FIXTURE_STAGE>/work"},
            "argv_scope": "Complete recorded argument vector with path/unit/UID/GID aliases",
            "argv": [sanitize(v) for v in argv],
            "worker_observation": {
                "non_root_uid": worker["uid"] > 0,
                "non_root_gid": worker["gid"] > 0,
                "source_readonly": worker["source_readonly"],
                "installed_readonly": worker["installed_readonly"],
                "namespace_kinds_recorded": sorted(worker["namespaces"]),
                "cgroup": sanitize(worker["cgroup"])},
            "tools_sha256": status["tools"],
            "payload_files_observed": status["payload_files"],
            "installed_files_observed": status["installed_files"],
            "protected_process_count_before": len(status["protected_before"]),
            "preservation": {k: status[k] for k in PRESERVATION},
            "unit_after": status["unit_after"],
            "unit_cgroup_absent": status["unit_cgroup_absent"],
            "temporary_fixture_remainders": status["temporary_fixture_remainders"],
            "launch_receipt_collection": collection_receipt(launch2_outer),
            "result_receipt_collection": collection_receipt(result2_outer),
            "tests": [{"ordinal": i, "id": ident, "result": outcome,
                       "source_definition": definitions[ident]}
                      for i, (_, ident, outcome) in enumerate(matches, 1)]},
        "limits": [
            "No runtime/compiler/VM operation was repeated to prepare or verify this extract.",
            "Native tool hashes and preservation/cleanup observations are historical records, not new inspections.",
            "The first failure is not part of the later 46-test pass.",
            "Temporary fixture remainders are empty; retained evidence stages are not claimed deleted.",
            "Same-account preparation and verification are not independent approval."]}
    bindings = {
        "schema": "agterm-native-installer-bindings/1",
        "original_records_and_driver": {
            name: receipt(data) for name, data in sorted(originals.items())},
        "source": {
            "commit": SOURCE,
            "input_scope": "Every file in the successful driver's recorded source_inputs; copied bytes also checked against the official public Git tree during preparation.",
            "inputs": source_records},
        "driver_source_checks": {
            "same_bytes_both_launches_and_completed_status": True,
            "git_query_timeout_seconds": 30,
            "service_query_timeout_seconds": 10,
            "service_wait_timeout_seconds": 210,
            "worker_selection": ["python3", "-B", "-m", "unittest", "discover",
                                 "-s", "<SOURCE77>/scripts/tests",
                                 "-p", "test_linux_install*.py", "-v"]}}
    projections = {"schema": "agterm-native-installer-log-projections/1",
                   "streams": streams}
    documents = dict(zip(DOCUMENTS, (report, bindings, projections)))
    rendered = json.dumps(documents)
    for private in replacements:
        require(private not in rendered, "private identity survived transformation")
    for pattern in (r"/home/[^/\s]+/", r"/Users/[^/\s]+/",
                    r"agterm-installer-test-[0-9a-f]+\.service",
                    r"ninstalltests\.[a-z0-9]+", r"(?:pid|mnt|net|ipc):\[\d+\]"):
        require(not re.search(pattern, rendered), "private value survived projection")
    return documents


def validate_candidate(candidate, expected):
    require(candidate == expected, "published projection differs from bound originals")


def negative_controls(expected, originals, sources):
    count = 0
    changes = (
        lambda c: c[DOCUMENTS[0]]["prerequisite_failure"].update(outcome="passed"),
        lambda c: c[DOCUMENTS[0]]["completed_attempt"]["tests"].pop(),
        lambda c: c[DOCUMENTS[0]]["completed_attempt"]["tests"][0].update(result="skipped"),
        lambda c: c[DOCUMENTS[0]]["completed_attempt"]["service_properties"].update(RuntimeMaxSec="1800"),
        lambda c: c[DOCUMENTS[0]]["completed_attempt"].update(unit_cgroup_absent=False),
        lambda c: c[DOCUMENTS[2]]["streams"]["completed_tests"].update(text="OK\n"))
    for change in changes:
        candidate = copy.deepcopy(expected)
        change(candidate)
        try:
            validate_candidate(candidate, expected)
        except ValueError:
            count += 1
        else:
            raise ValueError("negative projection control was accepted")
    changed_sources = dict(sources)
    changed_sources[TEST_FILES[0]] += b"\n# unreviewed change\n"
    try:
        make_documents(originals, changed_sources)
    except ValueError:
        count += 1
    else:
        raise ValueError("changed source bytes were accepted")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads(read_regular(package, "manifest.json"))
    require(set(p.name for p in package.iterdir()) ==
            set(manifest["files"]) | {"manifest.json"},
            "unexpected public package file set")
    for name, expected in manifest["files"].items():
        require(receipt(read_regular(package, name)) == expected,
                "public manifest mismatch: " + name)
    originals = read_originals(args.original_root)
    sources = source_inputs(originals, args.source_root)
    expected = make_documents(originals, sources)
    candidate = {name: json.loads(read_regular(package, name))
                 for name in DOCUMENTS}
    validate_candidate(candidate, expected)
    negatives = negative_controls(expected, originals, sources)
    print(json.dumps({
        "passed": True, "source": SOURCE,
        "original_records_and_driver_rehashed": len(originals),
        "source_inputs_rehashed": len(sources),
        "actual_test_names_and_results_verified": 46,
        "complete_log_projections_verified": 3,
        "negative_controls_rejected": negatives,
        "no_original_helper_import_or_execution": True,
        "no_runtime_compiler_or_VM_execution": True,
        "same_account_not_independent_approval": True}, indent=2))


if __name__ == "__main__":
    main()
