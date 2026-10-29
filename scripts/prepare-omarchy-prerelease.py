#!/usr/bin/env python3
"""Validate and download an existing trusted CI build; never execute its contents."""

import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import tarfile
import zipfile

REPOSITORY = "alexandru-savinov/agterm-omarchy"
WORKFLOW = ".github/workflows/ci.yml"
ARCHITECTURES = {"x86_64": 62, "aarch64": 183}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path, pages=False):
    command = ["gh", "api", f"repos/{REPOSITORY}/{path}"]
    if pages:
        command += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(command))


def validate_run(run, workflow, sha, branch_sha):
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Expected a full lowercase source SHA")
    require(run["repository"]["full_name"] == REPOSITORY, "Wrong source repository")
    require(run["head_repository"]["full_name"] == REPOSITORY, "Fork head rejected")
    require(run["path"] == WORKFLOW and run["workflow_id"] == workflow["id"]
            and run["name"] == "Omarchy CI", "Wrong CI workflow")
    require(run["status"] == "completed" and run["conclusion"] == "success", "CI is not green")
    require(run["event"] in ("push", "workflow_dispatch"), "PR merge runs cannot be promoted")
    require(run["head_branch"] in ("main", "omarchy"), "Untrusted source branch")
    require(run["head_sha"] == sha == branch_sha, "Source is not the requested current branch head")


def validate_jobs(jobs):
    required = {"Installer and scripts", "SwiftLint (Linux)", "CI"}
    for arch in ARCHITECTURES:
        required.update((f"Core tests ({arch})", f"GTK build and tests ({arch})"))
    for name in required:
        matches = [job for job in jobs if job["name"] == name]
        require(len(matches) == 1 and matches[0]["conclusion"] == "success", f"Missing successful {name}")
        if name.startswith("GTK build"):
            for step in ("swift test (agterm-linux)", "swift build Release/WMO (agterm-linux)",
                         "Validate staged Linux payload", "GTK AT-SPI smoke tests", "Upload tested runtime payload"):
                steps = [item for item in matches[0]["steps"] if item["name"] == step]
                require(len(steps) == 1 and steps[0]["conclusion"] == "success", f"Missing successful {step}")


def validate_payload(data, arch, sha):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        names = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts
                    and path.parts[0] == "agterm-stage", "Unsafe or unexpected tar path")
            require(member.name not in names, "Duplicate tar entry")
            names.add(member.name)
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(), "Special tar entry")
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(str(path.parent), member.linkname)
                                            if member.issym() else member.linkname)
                require(target.startswith("agterm-stage/") and not member.linkname.startswith("/"), "Escaping tar link")

        def read(name, limit):
            member = archive.getmember("agterm-stage/" + name)
            require(member.isfile() and member.size <= limit, f"Invalid {name}")
            return archive.extractfile(member).read()

        require(read("share/agterm/COMMIT", 128).decode().strip() == sha, "Embedded source mismatch")
        version = read("share/agterm/VERSION", 128).decode().strip()
        require(version == "dev", "Expected unchanged CI development version")
        for name in ("agterm-linux.bin", "agtermctl.bin", "zmx"):
            member = archive.getmember("agterm-stage/bin/" + name)
            require(member.isfile() and member.mode & 0o111, f"Missing executable {name}")
            header = archive.extractfile(member).read(20)
            require(header[:6] == b"\x7fELF\x02\x01" and len(header) == 20
                    and int.from_bytes(header[18:20], "little") == ARCHITECTURES[arch], f"Wrong ELF architecture: {name}")
        return version


def download_artifact(artifact, arch, sha):
    require(not artifact["expired"], "Expired artifact")
    require(artifact["workflow_run"]["head_sha"] == sha, "Artifact source mismatch")
    digest = artifact.get("digest", "")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "Missing artifact digest")
    data = subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"])
    require("sha256:" + hashlib.sha256(data).hexdigest() == digest, "Artifact transport digest mismatch")
    filename = f"agterm-omarchy-{arch}.tar.gz"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        require(archive.namelist() == [filename], "Unexpected artifact ZIP contents")
        payload = archive.read(filename)
    validate_payload(payload, arch, sha)
    return filename, payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", type=int)
    parser.add_argument("sha")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    require(args.run_id > 0, "Invalid run ID")
    run = api(f"actions/runs/{args.run_id}")
    require(run["head_branch"] in ("main", "omarchy"), "Untrusted source branch")
    branch = api(f"git/ref/heads/{run['head_branch']}")
    validate_run(run, api("actions/workflows/ci.yml"), args.sha, branch["object"]["sha"])
    jobs = api(f"actions/runs/{args.run_id}/attempts/{run['run_attempt']}/jobs?per_page=100", pages=True)
    validate_jobs([job for page in jobs for job in page["jobs"]])
    pages = api(f"actions/runs/{args.run_id}/artifacts?per_page=100", pages=True)
    artifacts = [artifact for page in pages for artifact in page["artifacts"]]
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"repository": REPOSITORY, "source_commit": args.sha, "source_branch": run["head_branch"],
                "source_run": run["html_url"], "source_run_attempt": run["run_attempt"],
                "source_workflow": WORKFLOW, "artifacts": []}
    checksums = []
    for arch in ARCHITECTURES:
        matches = [item for item in artifacts if item["name"] == f"agterm-omarchy-{arch}-{args.sha}"]
        require(len(matches) == 1, f"Missing unique {arch} artifact")
        artifact = matches[0]
        require(artifact["workflow_run"]["id"] == args.run_id, "Artifact run mismatch")
        require(datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00")) >=
                datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00")), "Artifact predates this run attempt")
        name, data = download_artifact(artifact, arch, args.sha)
        (args.output / name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        checksums.append(f"{digest}  {name}\n")
        evidence["artifacts"].append({"id": artifact["id"], "name": artifact["name"],
                                      "transport_digest": artifact["digest"], "filename": name, "sha256": digest})
    # Recheck the mutable run before allowing a privileged publishing job to consume these results.
    current = api(f"actions/runs/{args.run_id}")
    require(current["run_attempt"] == run["run_attempt"] and current["status"] == "completed"
            and current["conclusion"] == "success", "Run changed while downloading")
    (args.output / "promotion.json").write_text(json.dumps(evidence, indent=2) + "\n")
    checksums.append(f"{hashlib.sha256((args.output / 'promotion.json').read_bytes()).hexdigest()}  promotion.json\n")
    (args.output / "SHA256SUMS").write_text("".join(checksums))
    tag = f"omarchy-dev-{args.sha[:12]}-run{args.run_id}"
    (args.output / "release-notes.md").write_text(
        f"Experimental Omarchy development snapshot `{tag}`; not an upstream stable release.\n\n"
        f"Source: https://github.com/{REPOSITORY}/commit/{args.sha}\n\n"
        f"Successful build/test run: {run['html_url']} (attempt {run['run_attempt']}).\n\n"
        "Both native x86_64 and ARM64 hosted CI jobs passed core tests, Linux tests, Release builds, "
        "staged payload validation and GTK AT-SPI smoke gates. These hosted tests use Ubuntu/Xvfb; "
        "this release does not claim native Omarchy/Wayland acceptance for these exact bytes.\n\n"
        "Archives are unchanged CI artifacts, retain embedded version `dev`, and unpack into `agterm-stage/`. "
        "GTK4, libadwaita, WebKitGTK 6 and platform system libraries are required. "
        "Launch `agterm-stage/bin/agterm-linux`. No installer or desktop changes run automatically.\n\n"
        "`SHA256SUMS` covers both archives and `promotion.json`. GitHub build provenance describes "
        "the promotion workflow, not a recompilation; the additional signed promotion predicate records "
        "the validated original build run, source SHA and immutable artifact identities.\n\n"
        f"Verify: `gh attestation verify agterm-omarchy-aarch64.tar.gz --repo {REPOSITORY}`.\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"tag={tag}\nsha={args.sha}\n")
    print(f"Validated {tag}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Promotion rejected: {error}") from error
