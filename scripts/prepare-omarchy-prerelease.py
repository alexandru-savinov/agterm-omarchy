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
BRANCHES = ("main", "omarchy")
TAG = re.compile(r"omarchy-dev-[0-9a-f]{12}-run[1-9][0-9]*")
REQUIRED_JOBS = ("Installer and scripts", "SwiftLint (Linux)", "CI")
REQUIRED_ARCH_JOBS = ("Core tests ({arch})", "GTK build and tests ({arch})")
REQUIRED_GTK_STEPS = ("swift test (agterm-linux)", "swift build Release/WMO (agterm-linux)",
                      "Validate staged Linux payload", "GTK AT-SPI smoke tests", "Upload tested runtime payload")
# A versioned release also needs the native tar/readelf/ldd check its notes describe.
RELEASE_GTK_STEPS = ("Rehearse versioned release archive", "Upload release rehearsal evidence")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path, pages=False):
    command = ["gh", "api", f"repos/{REPOSITORY}/{path}"]
    if pages:
        command += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(command))


def absent(path):
    """True when the API answers 404; any other failure is an error, never a pass."""
    result = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/{path}", "--silent"], capture_output=True, text=True)
    if result.returncode == 0:
        return False
    require("HTTP 404" in result.stderr, f"Could not check {path}: {result.stderr.strip()}")
    return True


def promotion_tag(sha, run_id):
    tag = f"omarchy-dev-{sha[:12]}-run{run_id}"
    require(TAG.fullmatch(tag), f"Invalid promotion tag {tag}")
    return tag


def published(tag):
    return not (absent(f"git/ref/tags/{tag}") and absent(f"releases/tags/{tag}"))


def run_jobs(run):
    pages = api(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", pages=True)
    return [job for page in pages for job in page["jobs"]]


def run_artifacts(run):
    return [artifact for page in api(f"actions/runs/{run['id']}/artifacts?per_page=100", pages=True)
            for artifact in page["artifacts"]]


def select_artifacts(artifacts, run, sha):
    """The one artifact per architecture this run attempt uploaded, or ValueError."""
    selected = {}
    for arch in ARCHITECTURES:
        matches = [item for item in artifacts if item["name"] == f"agterm-omarchy-{arch}-{sha}"]
        require(len(matches) == 1, f"Missing unique {arch} artifact")
        artifact = matches[0]
        require(artifact["workflow_run"]["id"] == run["id"], "Artifact run mismatch")
        require(not artifact["expired"], "Expired artifact")
        require(datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00")) >=
                datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00")), "Artifact predates this run attempt")
        selected[arch] = artifact
    return selected


def parse_native_archives(text, sha):
    """Validate the native gate's ci-run archives output for both architectures."""
    archives = json.loads(text)
    require(isinstance(archives, dict) and archives.keys() == ARCHITECTURES.keys(),
            "Native evidence must contain exactly x86_64 and aarch64 archives")
    for arch, record in archives.items():
        require(isinstance(record, dict), f"Invalid native archive record for {arch}")
        artifact_id = record.get("id")
        require(type(artifact_id) is int and artifact_id > 0, f"Invalid native artifact ID for {arch}")
        require(record.get("name") == f"agterm-omarchy-{arch}-{sha}"
                and record.get("filename") == f"agterm-omarchy-{arch}.tar.gz",
                f"Native archive names differ from the expected CI archive for {arch}")
        digest, transport = record.get("sha256"), record.get("transport_digest")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
                f"Invalid native archive SHA-256 for {arch}")
        require(isinstance(transport, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", transport),
                f"Invalid native artifact transport digest for {arch}")
        require(record.get("attestation") == "verified", f"Native archive attestation is not verified for {arch}")
    return archives


def discover():
    """Newest run at a current trusted branch head that main() would accept for an unpublished tag."""
    workflow = api("actions/workflows/ci.yml")
    candidates = []
    for branch in BRANCHES:
        head = api(f"git/ref/heads/{branch}")["object"]["sha"]
        pages = api(f"actions/workflows/ci.yml/runs?branch={branch}&head_sha={head}&status=success&per_page=100",
                    pages=True)
        for run in (run for page in pages for run in page["workflow_runs"]):
            try:
                validate_run(run, workflow, head, head)
                validate_jobs(run_jobs(run))
                select_artifacts(run_artifacts(run), run, head)
            except ValueError:
                continue
            # Outside the try: a failed lookup is an error, not a published tag.
            if not published(promotion_tag(head, run["id"])):
                candidates.append(run)
    return max(candidates, key=lambda run: run["created_at"], default=None)


def validate_run(run, workflow, sha, branch_sha):
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Expected a full lowercase source SHA")
    require(run["repository"]["full_name"] == REPOSITORY, "Wrong source repository")
    require(run["head_repository"]["full_name"] == REPOSITORY, "Fork head rejected")
    require(run["path"] == WORKFLOW and run["workflow_id"] == workflow["id"]
            and run["name"] == "Omarchy CI", "Wrong CI workflow")
    require(run["status"] == "completed" and run["conclusion"] == "success", "CI is not green")
    require(run["event"] in ("push", "workflow_dispatch"), "PR merge runs cannot be promoted")
    require(run["head_branch"] in BRANCHES, "Untrusted source branch")
    require(run["head_sha"] == sha == branch_sha, "Source is not the requested current branch head")


def validate_jobs(jobs, release=False):
    required = set(REQUIRED_JOBS)
    for arch in ARCHITECTURES:
        required.update(name.format(arch=arch) for name in REQUIRED_ARCH_JOBS)
    for name in required:
        matches = [job for job in jobs if job["name"] == name]
        require(len(matches) == 1 and matches[0]["conclusion"] == "success", f"Missing successful {name}")
        if name.startswith("GTK build"):
            for step in REQUIRED_GTK_STEPS + (RELEASE_GTK_STEPS if release else ()):
                steps = [item for item in matches[0]["steps"] if item["name"] == step]
                require(len(steps) == 1 and steps[0]["conclusion"] == "success", f"Missing successful {step}")


def resolve_member_path(parts, links, depth=0):
    """Resolve a path the way extraction would, following symlinks among the archive's own members."""
    require(depth <= 40, "Tar link chain too deep")
    resolved = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            # Leaving agterm-stage, even transiently, escapes the directory the payload is extracted into.
            require(len(resolved) > 1, "Escaping tar link")
            resolved.pop()
            continue
        resolved.append(part)
        link = links.get("/".join(resolved))
        if link is not None:
            require(not link.startswith("/"), "Escaping tar link")
            resolved = resolve_member_path(resolved[:-1] + list(PurePosixPath(link).parts), links, depth + 1)
    require(resolved[:1] == ["agterm-stage"], "Escaping tar link")
    return resolved


def resolve_member_links(members):
    for member in members:
        # Only canonical spellings ("a/b", no "./" or "//"), so the link map and every lookup share one key.
        require(member.name.rstrip("/") == str(PurePosixPath(member.name)), "Non-canonical tar path")
    links = {member.name.rstrip("/"): member.linkname for member in members if member.issym()}
    for member in members:
        parent = list(PurePosixPath(member.name).parent.parts)
        # A member written below a symlink would land wherever that link points. The top-level
        # agterm-stage entry has no parent inside the archive to resolve.
        require(not parent or resolve_member_path(parent, links) == parent, "Tar member below a link")
        if member.issym():
            resolve_member_path(parent + list(PurePosixPath(member.linkname).parts), links)
        elif member.islnk():
            resolve_member_path(list(PurePosixPath(member.linkname).parts), links)


def validate_payload(data, arch, sha):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        names = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts
                    and path.parts[0] == "agterm-stage", "Unsafe or unexpected tar path")
            # Keyed like the link map, so two spellings of one path count as one entry.
            key = member.name.rstrip("/")
            require(member.isdir() or key == member.name, "Trailing slash on a non-directory tar entry")
            require(key not in names, "Duplicate tar entry")
            names.add(key)
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(), "Special tar entry")
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(str(path.parent), member.linkname)
                                            if member.issym() else member.linkname)
                require(target.startswith("agterm-stage/") and not member.linkname.startswith("/"), "Escaping tar link")
        resolve_member_links(archive.getmembers())

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
        for name in ("agterm-linux", "agtermctl"):
            path = "agterm-stage/bin/" + name
            require(path in names, f"Missing executable wrapper {name}")
            member = archive.getmember(path)
            require(member.isfile() and member.mode & 0o111 and member.size > 0,
                    f"Invalid executable wrapper {name}")
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


def release_module():
    """omarchy-release.py, loaded on demand: it imports this module itself."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("omarchy_release", Path(__file__).resolve().parent / "omarchy-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def on_main(sha):
    """True when SHA is the current main head or one of its ancestors."""
    head = api("git/ref/heads/main")["object"]["sha"]
    return sha == head or api(f"compare/{sha}...{head}")["status"] == "ahead"


def source_base(release, sha):
    """The upstream base the source commit itself declares, not this checkout's."""
    import base64
    content = api(f"contents/{WORKFLOW}?ref={sha}")
    require(content.get("encoding") == "base64", "Unreadable source ci.yml")
    return release.upstream_base(base64.b64decode(content["content"]).decode())


def released_versions(release):
    """Versions of every published non-prerelease release whose whole tag matches the omarchy-pkgs watch."""
    versions = []
    for page in api("releases?per_page=100", pages=True):
        for item in page:
            match = release.WATCH_TAG.fullmatch(item["tag_name"])
            if match and not item["draft"] and not item["prerelease"]:
                versions.append(match["version"])
    return versions


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def is_latest(version, existing):
    """GitHub's latest marker follows the highest version, so a backport never takes it."""
    return all(version_key(version) > version_key(other) for other in existing)


def next_version(release, base=None):
    """The next unpublished serial for BASE, by default this checkout's upstream base."""
    base = release.upstream_base() if base is None else base
    serials = [int(version.rsplit(".", 1)[1]) for version in released_versions(release)
               if version.startswith(base + ".") and version.count(".") == base.count(".") + 1]
    return f"{base}.{max(serials, default=0) + 1}"


def write_outputs(values):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("".join(f"{key}={value}\n" for key, value in values.items()))


def discover_commit(sha):
    """Newest successful main CI run at SHA, which must be on main, for a versioned release of that commit."""
    require(re.fullmatch(r"[0-9a-f]{40}", sha) and on_main(sha), f"{sha} is not a commit on main")
    workflow = api("actions/workflows/ci.yml")
    pages = api(f"actions/workflows/ci.yml/runs?branch=main&head_sha={sha}&status=success&per_page=100", pages=True)
    candidates = []
    for run in (run for page in pages for run in page["workflow_runs"]):
        if run["head_branch"] != "main":
            continue
        try:
            validate_run(run, workflow, sha, sha)
            validate_jobs(run_jobs(run), release=True)
            select_artifacts(run_artifacts(run), run, sha)
        except ValueError:
            continue
        candidates.append(run)
    return max(candidates, key=lambda run: run["created_at"], default=None)


def discover_releasable(limit=20):
    """Newest successful main CI run, at any main commit, that release mode accepts.

    A PR dry run uses it so an unfinished CI run at the main head falls back to the last releasable commit
    instead of checking nothing."""
    workflow = api("actions/workflows/ci.yml")
    runs = api(f"actions/workflows/ci.yml/runs?branch=main&status=success&per_page={limit}")["workflow_runs"]
    for run in sorted(runs, key=lambda run: run["created_at"], reverse=True):
        sha = run["head_sha"]
        if run["head_branch"] != "main" or not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        try:
            validate_run(run, workflow, sha, sha)
            validate_jobs(run_jobs(run), release=True)
            select_artifacts(run_artifacts(run), run, sha)
        except ValueError:
            continue
        if on_main(sha):
            return run
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true",
                        help="print the newest promotable source run and SHA instead of validating one")
    parser.add_argument("--commit", help="with --discover: the newest promotable run at this main commit instead")
    parser.add_argument("--releasable", action="store_true",
                        help="with --discover: the newest main run release mode accepts, at any main commit")
    parser.add_argument("--next-version", action="store_true", help="print the next unpublished release version")
    parser.add_argument("--base-of", metavar="SHA",
                        help="with --next-version, use the upstream base commit SHA declares instead of this checkout's")
    parser.add_argument("--release", metavar="VERSION",
                        help="build versioned release assets for tag omarchy-vVERSION instead of a dev prerelease")
    parser.add_argument("--source-attempt", type=int,
                        help="require the exact CI attempt that passed the automatic release gates")
    parser.add_argument("--native-archives", help="require the native gate's attested CI archives JSON")
    parser.add_argument("--auto-release-run-id", type=int, help="record the automatic release orchestration run")
    parser.add_argument("run_id", type=int, nargs="?")
    parser.add_argument("sha", nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    require(args.base_of is None or args.next_version, "--base-of needs --next-version")
    require((args.source_attempt is None) == (args.native_archives is None),
            "--source-attempt and --native-archives must be provided together")
    if args.source_attempt is not None:
        require(args.source_attempt > 0 and args.release and not (args.next_version or args.discover),
                "--source-attempt requires a positive attempt and versioned release preparation")
    require(args.auto_release_run_id is None or (args.auto_release_run_id > 0 and args.source_attempt is not None),
            "--auto-release-run-id requires positive ID and gated release evidence")
    if args.next_version:
        release = release_module()
        version = next_version(release, source_base(release, args.base_of) if args.base_of else None)
        write_outputs({"version": version})
        print(version)
        return
    if args.discover:
        require(args.run_id is None, "--discover takes no arguments")
        require(not (args.commit and args.releasable), "--commit and --releasable are exclusive")
        run = discover_commit(args.commit) if args.commit else discover_releasable() if args.releasable else discover()
        write_outputs({"run": run["id"], "sha": run["head_sha"]} if run else {"run": ""})
        print(f"{run['id']} {run['head_sha']}" if run else "No promotable source run")
        return
    require(None not in (args.run_id, args.sha, args.output), "Expected RUN_ID SHA OUTPUT")
    require(args.run_id > 0, "Invalid run ID")
    release = release_module() if args.release else None
    if release:
        release.check_version(args.release)
    else:
        tag = promotion_tag(args.sha, args.run_id)
    gated = parse_native_archives(args.native_archives, args.sha) if args.native_archives is not None else {}
    run = api(f"actions/runs/{args.run_id}")
    require(run["id"] == args.run_id, "Requested run ID mismatch")
    require(args.source_attempt is None or run["run_attempt"] == args.source_attempt,
            "CI attempt differs from the attempt tested by the automatic release gates")
    if release:
        # A release names its commit explicitly, so main may have moved on; it must still contain it.
        require(run["head_branch"] == "main", "Releases are cut only from main CI runs")
        require(on_main(args.sha), f"{args.sha} is not a commit on main")
        branch_sha = args.sha
    else:
        require(run["head_branch"] in BRANCHES, "Untrusted source branch")
        branch_sha = api(f"git/ref/heads/{run['head_branch']}")["object"]["sha"]
    validate_run(run, api("actions/workflows/ci.yml"), args.sha, branch_sha)
    validate_jobs(run_jobs(run), release=bool(release))
    if release:
        tag = release.release_tag(args.release, source_base(release, args.sha))
    artifacts = select_artifacts(run_artifacts(run), run, args.sha)
    for arch, expected in gated.items():
        require(artifacts[arch]["id"] == expected["id"], f"Selected artifact ID differs from native evidence for {arch}")
        require(artifacts[arch].get("digest") == expected["transport_digest"],
                f"Selected artifact transport digest differs from native evidence for {arch}")
    # A dry run must fail where publication would: tag creation refuses an existing ref.
    require(not published(tag), f"Tag or release {tag} already exists")
    latest = is_latest(args.release, released_versions(release)) if release else False
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"repository": REPOSITORY, "source_commit": args.sha, "source_branch": run["head_branch"],
                "source_run": run["html_url"], "source_run_attempt": run["run_attempt"],
                "source_workflow": WORKFLOW, "artifacts": []}
    if release:
        evidence.update(tag=tag, version=args.release, latest=latest, releases=[])
    if args.auto_release_run_id is not None:
        evidence["auto_release_run_id"] = args.auto_release_run_id
    checksums = []
    for arch, artifact in artifacts.items():
        name, data = download_artifact(artifact, arch, args.sha)
        digest = hashlib.sha256(data).hexdigest()
        if gated:
            require(digest == gated[arch]["sha256"], f"Archive SHA-256 differs from native evidence for {arch}")
        evidence["artifacts"].append({"id": artifact["id"], "name": artifact["name"],
                                      "transport_digest": artifact["digest"], "filename": name, "sha256": digest})
        if release:
            asset = release.release(data, arch, args.sha, args.release, args.output)
            evidence["releases"].append(asset)
            name, digest = asset["asset"], asset["release_archive_sha256"]
        else:
            (args.output / name).write_bytes(data)
        checksums.append(f"{digest}  {name}\n")
    # Recheck the mutable run before allowing a privileged publishing job to consume these results.
    current = api(f"actions/runs/{args.run_id}")
    require(current["id"] == args.run_id, "Requested run ID mismatch")
    require(current["run_attempt"] == run["run_attempt"] and current["status"] == "completed"
            and current["conclusion"] == "success", "Run changed while downloading")
    (args.output / "promotion.json").write_text(json.dumps(evidence, indent=2) + "\n")
    checksums.append(f"{hashlib.sha256((args.output / 'promotion.json').read_bytes()).hexdigest()}  promotion.json\n")
    (args.output / "SHA256SUMS").write_text("".join(checksums))
    notes = release_notes if release else development_notes
    (args.output / "release-notes.md").write_text(notes(tag, args.sha, run, args.release))
    write_outputs({"tag": tag, "sha": args.sha} | ({"version": args.release, "latest": str(latest).lower()}
                                                   if release else {}))
    print(f"Validated {tag}")


EGL_COMPAT_NOTES = (
    "For archives that include the Mesa/GTK EGL compatibility retry, set "
    "`AGTERM_DISABLE_EGL_COMPAT=1` before starting a fresh agterm-linux process to test a system fix. "
    "Track the retry in [#485](https://github.com/alexandru-savinov/agterm-omarchy/issues/485).\n\n"
)


def development_notes(tag, sha, run, _version):
    return (
        f"Experimental Omarchy development snapshot `{tag}`; not an upstream stable release.\n\n"
        f"Source: https://github.com/{REPOSITORY}/commit/{sha}\n\n"
        f"Successful build/test run: {run['html_url']} (attempt {run['run_attempt']}).\n\n"
        "Both native x86_64 and ARM64 hosted CI jobs passed core tests, Linux tests, Release builds, "
        "staged payload validation and GTK AT-SPI smoke gates. These hosted tests use Ubuntu/Xvfb; "
        "this release does not claim native Omarchy/Wayland acceptance for these exact bytes.\n\n"
        "Archives are unchanged CI artifacts, retain embedded version `dev`, and unpack into `agterm-stage/`. "
        "GTK4, libadwaita, WebKitGTK 6 and platform system libraries are required. "
        "Launch `agterm-stage/bin/agterm-linux`. No installer or desktop changes run automatically.\n\n"
        f"{EGL_COMPAT_NOTES}"
        "`SHA256SUMS` covers both archives and `promotion.json`. GitHub build provenance describes "
        "the promotion workflow, not a recompilation; the additional signed promotion predicate records "
        "the validated original build run, source SHA and immutable artifact identities.\n\n"
        f"Verify: `gh attestation verify agterm-omarchy-aarch64.tar.gz --repo {REPOSITORY} "
        f"--signer-workflow {REPOSITORY}/.github/workflows/prerelease-omarchy.yml "
        "--source-ref refs/heads/main`.\n")


def release_notes(tag, sha, run, version):
    return (
        f"agterm for Omarchy {version}, built from upstream agterm {version.rsplit('.', 1)[0]} plus the Linux port.\n\n"
        f"Source: https://github.com/{REPOSITORY}/commit/{sha}\n\n"
        f"Successful build/test run: {run['html_url']} (attempt {run['run_attempt']}).\n\n"
        "Both native x86_64 and ARM64 hosted CI jobs passed core tests, Linux tests, Release builds, "
        "staged payload validation, GTK AT-SPI smoke gates and a native check of the release archive "
        "(root ownership, `$ORIGIN/../lib` RUNPATH, libraries resolved from the archive). "
        "Hosted tests use Ubuntu/Xvfb; native Omarchy/Wayland acceptance is recorded separately.\n\n"
        f"Each `agterm-omarchy-{version}-<arch>.tar.gz` is the tested CI archive with only "
        f"`share/agterm/VERSION` set to `{version}` and numeric root ownership; `promotion.json` records "
        "both digests. Archives unpack into `agterm-stage/`; licenses are under `share/licenses/agterm/`. "
        "GTK4, libadwaita, WebKitGTK 6 and platform system libraries are required.\n\n"
        f"{EGL_COMPAT_NOTES}"
        "`SHA256SUMS` covers both archives and `promotion.json`. GitHub build provenance describes "
        "the promotion workflow; the additional signed promotion predicate records the validated build run, "
        "source SHA and artifact identities.\n\n"
        f"Verify: `gh attestation verify agterm-omarchy-{version}-aarch64.tar.gz --repo {REPOSITORY} "
        f"--signer-workflow {REPOSITORY}/.github/workflows/prerelease-omarchy.yml --source-ref refs/heads/main`.\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Promotion rejected: {error}") from error
