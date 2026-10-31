#!/usr/bin/env python3
"""Run a bounded real Bandit scan or upload its preserved report to code scanning."""

import argparse
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

VERSION = "1.9.4"
WHEEL_SHA256 = "f89ffa663767f5a0585ea075f01020207e966a9c0f2b9ef56a57c7963a3f6f8e"
CATEGORY = "bandit-orchestration-packaging"
REPOSITORY = "alexandru-savinov/agterm-omarchy"
ROOT = Path(__file__).resolve().parents[2]
SCOPE = (
    "scripts/github/channel_probe.py",
    "scripts/github/queue.py",
    "scripts/prepare-omarchy-prerelease.py",
    "scripts/zmx-patches/check.py",
    "docs/omarchy-pages/build.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def scan(output):
    require(git("status", "--porcelain") == "", "Scan requires a clean committed checkout")
    commit = git("rev-parse", "HEAD")
    require(commit == os.environ["GITHUB_SHA"], "Checkout does not match recorded workflow SHA")
    report = json.loads((output / "install-report.json").read_text())
    packages = [item for item in report["install"] if item["metadata"]["name"].lower() == "bandit"]
    require(len(packages) == 1 and packages[0]["metadata"]["version"] == VERSION, "Wrong installed Bandit release")
    require(packages[0]["download_info"]["archive_info"]["hashes"]["sha256"] == WHEEL_SHA256, "Bandit wheel checksum mismatch")
    sources = {}
    for name in SCOPE:
        path = ROOT / name
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(ROOT), "Scope must stay in repository files")
        git("ls-files", "--error-unmatch", name)
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    command = [sys.executable, "-m", "bandit", "--format", "sarif", "--output", str(output / "bandit.raw.sarif"),
               "--severity-level", "all", "--confidence-level", "all", "--ignore-nosec", *SCOPE]
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    (output / "bandit.log").write_text(result.stdout)
    print(result.stdout, end="")
    require(result.returncode in (0, 1), f"Bandit execution failed: {result.returncode}")
    sarif = json.loads((output / "bandit.raw.sarif").read_text())
    require(sarif["version"] == "2.1.0" and len(sarif["runs"]) == 1, "Unexpected SARIF structure")
    run = sarif["runs"][0]
    require(run["tool"]["driver"]["name"] == "Bandit" and run["tool"]["driver"]["version"] == VERSION, "Unexpected SARIF tool identity")
    for invocation in run.get("invocations", []):
        require(invocation.get("executionSuccessful") is True, "Scanner invocation did not succeed")
        require(not invocation.get("toolConfigurationNotifications"), "Scanner skipped files; inspect raw report")
    # Only add an analysis identity. Keep all actual scanner rules/results unchanged.
    run["automationDetails"] = {"id": CATEGORY + "/"}
    write_json(output / "bandit.sarif", sarif)
    identity = {"repository": REPOSITORY, "commit": commit, "ref": os.environ["GITHUB_REF"],
                "category": CATEGORY, "tool": "Bandit", "version": VERSION, "started_at": started,
                "source_sha256": sources, "scanner_exit_code": result.returncode,
                "results_count": len(run.get("results", [])), "command": command[1:]}
    write_json(output / "scan-identity.json", identity)
    print(f"Actual Bandit results: {identity['results_count']}; scanner exit: {result.returncode}; source: {commit}")
    print("Findings are scanner signals requiring triage, not confirmed vulnerabilities.")


def api(path, payload=None):
    command = ["gh", "api", f"repos/{REPOSITORY}/code-scanning/{path}"]
    data = None
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
        data = json.dumps(payload)
    return json.loads(subprocess.check_output(command, input=data, text=True))


def upload(output):
    identity = json.loads((output / "scan-identity.json").read_text())
    require(os.environ["GITHUB_REPOSITORY"] == REPOSITORY, "Wrong upload repository")
    require(identity["commit"] == os.environ["GITHUB_SHA"] == git("rev-parse", "HEAD"), "Wrong upload commit")
    require(identity["ref"] == os.environ["GITHUB_REF"] == "refs/heads/main", "Upload is restricted to reviewed main")
    payload = {"commit_sha": identity["commit"], "ref": identity["ref"], "tool_name": "Bandit",
               "started_at": identity["started_at"], "checkout_uri": ROOT.as_uri(), "validate": True,
               "sarif": base64.b64encode(gzip.compress((output / "bandit.sarif").read_bytes())).decode()}
    receipt = api("sarifs", payload)
    write_json(output / "upload-receipt.json", receipt)
    for _ in range(24):
        status = api(f"sarifs/{receipt['id']}")
        write_json(output / "upload-status.json", status)
        if status["processing_status"] == "complete":
            break
        require(status["processing_status"] != "failed", f"SARIF processing failed: {status}")
        time.sleep(5)
    else:
        raise ValueError("SARIF processing did not complete within two minutes; retain receipt for inspection")
    analyses = api(f"analyses?sarif_id={receipt['id']}")
    write_json(output / "analyses.json", analyses)
    require(len(analyses) == 1, "Expected exactly one completed analysis")
    analysis = analyses[0]
    require(analysis["commit_sha"] == identity["commit"] and analysis["ref"] == identity["ref"], "Analysis source identity mismatch")
    require(analysis["tool"]["name"] == "Bandit" and analysis["tool"]["version"] == VERSION, "Analysis tool identity mismatch")
    require(analysis["category"].rstrip("/") == CATEGORY, "Analysis category mismatch")
    require(not analysis.get("error"), "Analysis reported an ingestion error")
    require(analysis["results_count"] == identity["results_count"], "GitHub result count differs from actual report")
    summary = f"Bandit {VERSION}: {analysis['results_count']} actual results; analysis {analysis['id']}; source {identity['commit']}; category {analysis['category']}\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary + "Findings require triage; successful ingestion is not remediation.\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("scan", "upload"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (scan if args.mode == "scan" else upload)(output)


if __name__ == "__main__":
    main()
