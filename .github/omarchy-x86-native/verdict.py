#!/usr/bin/env python3
"""Decide the native gates' verdict from each leg's evidence; `passed` is the release safety boundary.

`passed` is true only when every requested architecture has one record from this run attempt that shows the
resolved bytes (the host's record of the identity and sha256 it downloaded, matching the source job's), a
verified attestation, the resolved embedded commit, and PASS on all four gates. Records from an earlier
attempt are never reused: re-running only failed jobs leaves the other leg without a record, so re-run all.
"""

import argparse
import json
import os
from pathlib import Path

GATES = {"ordinary": "PASS 7/7 ", "live": "PASS 9/9 ", "probe": "PASS 10/10", "uninstall": "PASS "}
ARCHITECTURES = ("x86_64", "aarch64")


def rows(path):
    """gates.tsv as a dict; a malformed or repeated row is an error, never silently merged."""
    table = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0]:
            raise ValueError(f"{path.name}:{number} is not a key/value row")
        if fields[0] in table:
            raise ValueError(f"{path.name}:{number} repeats {fields[0]}")
        table[fields[0]] = fields[1]
    return table


def check_leg(directory, arch, expected):
    """(gates rows, archive entry, attested, errors) for one architecture's evidence directory."""
    errors = []
    try:
        table = rows(directory / "gates.tsv")
        host = json.loads((directory / "host-record.json").read_text())
    except (OSError, ValueError) as error:
        return {}, None, False, [f"{arch}: unreadable evidence ({error})"]
    identity = expected["identities"].get(arch)
    checks = {
        "host record architecture": host.get("arch") == arch,
        "resolved artifact identity": identity is not None and host.get("identity") == identity,
        "host commit": host.get("commit") == expected["sha"],
        "tested architecture": table.get("host_arch") == arch,
        "payload commit": table.get("payload_commit") == expected["sha"],
        "archive sha256": bool(host.get("sha256")) and table.get("archive") == host.get("sha256"),
        "installed bytes": table.get("installed_vs_stage", "").startswith("PASS "),
    }
    if expected["source"] == "release":
        checks["release asset sha256"] = identity is not None and host.get("sha256") == identity.get("sha256")
    for name, prefix in GATES.items():
        value = table.get(name, "")
        checks[f"{name} gate"] = value == prefix if name == "probe" else value.startswith(prefix)
    errors += [f"{arch}: {name} does not match" for name, ok in checks.items() if not ok]
    archive = dict(identity or {}, filename=host.get("archive"), sha256=host.get("sha256"),
                   attestation=host.get("attestation"))
    return table, archive, host.get("attestation") == "verified", errors


def decide(evidence, expected, run_id, attempt, source_result, gates_result):
    errors = [] if source_result == "success" else [f"source job {source_result}"]
    if gates_result != "success":
        errors.append(f"gates jobs {gates_result}")
    arches = expected.get("arches")
    if not (isinstance(arches, list) and arches and len(set(arches)) == len(arches) and set(arches) <= set(ARCHITECTURES)):
        return {"passed": False, "attested": False, "errors": errors + [f"invalid architecture list {arches!r}"],
                "results": {}, "archives": {}}
    results, archives, attested = {}, {}, True
    for arch in expected["arches"]:
        name = f"omarchy-native-gates-{arch}-{expected['channel']}-{expected['label']}-{run_id}-{attempt}"
        directory = evidence / name
        if not directory.is_dir():
            errors.append(f"{arch}: no evidence from attempt {attempt}; re-run all jobs, not only failed ones")
            attested = False
            continue
        table, archive, leg_attested, leg_errors = check_leg(directory, arch, expected)
        results[arch], archives[arch] = table, archive
        attested = attested and leg_attested
        errors += leg_errors
    return {"passed": not errors and attested, "attested": attested, "errors": errors,
            "results": results, "archives": archives}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True, help="the source job's resolution, as JSON")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--source-result", required=True)
    parser.add_argument("--gates-result", required=True)
    parser.add_argument("--attestation", choices=("required", "report-only"), required=True)
    args = parser.parse_args()
    verdict = decide(args.evidence, json.loads(args.expected.read_text()), args.run_id, args.attempt,
                     args.source_result, args.gates_result)
    # report-only is the pull_request self-test: it may finish green on unattested archives, and never passes.
    if args.attestation == "report-only":
        verdict["passed"] = False
    for error in verdict["errors"]:
        print(f"::error::{error}")
    if not verdict["attested"]:
        print("::warning::not every archive has a verified build attestation, so passed is false")
    if args.attestation == "report-only":
        print("::warning::attestation: report-only, so passed is false")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key in ("passed", "attested"):
                output.write(f"{key}={str(verdict[key]).lower()}\n")
            for key in ("results", "archives"):
                output.write(f"{key}={json.dumps(verdict[key], sort_keys=True)}\n")
    print(json.dumps({key: verdict[key] for key in ("passed", "attested", "archives")}, indent=2, sort_keys=True))
    failed = verdict["errors"] or (args.attestation == "required" and not verdict["attested"])
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
