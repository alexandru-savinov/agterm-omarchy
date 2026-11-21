#!/usr/bin/env python3
"""Turn a validated Omarchy CI archive into a versioned release asset; never execute its contents.

The only content change is `share/agterm/VERSION`. Ownership becomes numeric root:root and the
gzip header carries no timestamp or name, so the same input and version always give the same bytes.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tarfile

SCRIPTS = Path(__file__).resolve().parent
CI_WORKFLOW = SCRIPTS.parent / ".github/workflows/omarchy-ci.yml"
# omarchy-pkgs `upstream-watch` matches this against the WHOLE tag of non-prerelease GitHub releases.
WATCH_TAG = re.compile(r"omarchy-v(?P<version>[0-9]+(?:\.[0-9]+)*)")
# Stricter than the watch: no leading zeros, so pacman's vercmp and this repository order versions alike.
VERSION = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*")
RUNPATH = "$ORIGIN/../lib"
STAGE = "agterm-stage/"
VERSION_MEMBER = STAGE + "share/agterm/VERSION"
EXECUTABLES = ("bin/agterm-linux.bin", "bin/agtermctl.bin")
NOTICES = STAGE + "share/licenses/agterm/"
THEMES = STAGE + "share/ghostty/themes/"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


promotion = load("promotion", "prepare-omarchy-prerelease.py")
runpath = load("runpath", "linux-runpath.py")
require = promotion.require


def upstream_base(text=None):
    """UPSTREAM_BASE_TAG from omarchy-ci.yml TEXT, by default this checkout's."""
    text = CI_WORKFLOW.read_text() if text is None else text
    match = re.search(r'^  UPSTREAM_BASE_TAG: "v([0-9.]+)"$', text, re.MULTILINE)
    require(match and VERSION.fullmatch(match[1]), "omarchy-ci.yml has no UPSTREAM_BASE_TAG")
    return match[1]


def check_version(version, rehearsal=False):
    """The base-independent rules, checkable before any source is read."""
    require(isinstance(version, str) and VERSION.fullmatch(version), f"Invalid release version {version!r}")
    serial = int(version.rsplit(".", 1)[1])
    require(serial == 0 if rehearsal else serial >= 1,
            "Release serial 0 is reserved for CI rehearsals" if not rehearsal else "A rehearsal uses serial 0")


def release_tag(version, base, rehearsal=False):
    """The tag for VERSION, which must be BASE.N; N is 0 only for a rehearsal, which is never published."""
    check_version(version, rehearsal)
    require(version.startswith(base + ".") and version.count(".") == base.count(".") + 1,
            f"Release version {version} is not {base}.N for upstream base {base}")
    tag = f"omarchy-v{version}"
    match = WATCH_TAG.fullmatch(tag)
    require(match and match["version"] == version, f"{tag} does not match the omarchy-pkgs watch pattern")
    return tag


def asset_name(version, arch):
    require(arch in promotion.ARCHITECTURES, f"Unknown architecture {arch}")
    return f"agterm-omarchy-{version}-{arch}.tar.gz"


def members(data):
    """[(TarInfo, bytes or None)] in archive order."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return [(member, archive.extractfile(member).read() if member.isfile() else None)
                for member in archive.getmembers()]


def stamp(data, version):
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=6, mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for member, content in members(data):
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            if member.name == VERSION_MEMBER:
                content = f"{version}\n".encode()
                member.size = len(content)
            archive.addfile(member, io.BytesIO(content) if content is not None else None)
    return output.getvalue()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_release(source, release, arch, sha, version):
    """Evidence that RELEASE is SOURCE with only VERSION changed, root-owned, with clean search paths."""
    require(promotion.validate_payload(source, arch, sha) == "dev", "Source is not a CI development archive")
    before, after = members(source), members(release)
    require(len(before) == len(after), "Release member count differs from the tested archive")
    fields = ("name", "type", "mode", "linkname")
    files = {}
    for (old, old_data), (new, new_data) in zip(before, after):
        require(all(getattr(old, field) == getattr(new, field) for field in fields) and int(old.mtime) == new.mtime,
                f"Release member {new.name} differs from the tested archive")
        require(new.uid == 0 and new.gid == 0 and new.uname in ("", "root") and new.gname in ("", "root"),
                f"Release member {new.name} is not owned by root:root")
        if new.name == VERSION_MEMBER:
            require(new_data == f"{version}\n".encode(), "Release VERSION is not the release version")
        else:
            require(old.size == new.size and old_data == new_data, f"Release member {new.name} changed content")
        if new_data is not None:
            files[new.name] = new_data
    for name in EXECUTABLES:
        try:
            paths = runpath.Elf(files[STAGE + name]).search_paths()
        except ValueError as error:
            paths = error
        require(paths == {"RUNPATH": RUNPATH}, f"{name} RUNPATH is not {RUNPATH}: {paths}")
    for name, content in files.items():
        if name.startswith((STAGE + "bin/", STAGE + "lib/")) and runpath.is_elf(content):
            paths = runpath.Elf(content).search_paths()
            require(all(runpath.relative_search_path(value) for value in paths.values()),
                    f"{name} has an absolute search path: {paths}")
    require(files.get(NOTICES + "LICENSE", b"").startswith(b"MIT License"), "Missing repository LICENSE notice")
    require(json.loads(files.get(NOTICES + "payload.json", b"{}")).get("unresolved_libraries") == [],
            "Bundled libraries without reviewed notices")
    validate_themes(files)
    return {"asset": asset_name(version, arch), "version": version, "arch": arch, "source_commit": sha,
            "tested_archive_sha256": digest(source), "release_archive_sha256": digest(release),
            "members": len(after), "changed_members": [VERSION_MEMBER], "owner": "0:0", "runpath": RUNPATH}


def validate_themes(files):
    """Every shipped theme is one the archive's own notice inventory resolves to a pinned author license."""
    inventory = json.loads(files.get(NOTICES + "inventory.json", b"{}"))
    licensed = {theme["file"]: theme["sha256"] for theme in inventory.get("themes", {}).get("files", [])
                if theme.get("attribution") == "resolved"}
    shipped = {name[len(THEMES):]: content for name, content in files.items()
               if name.startswith(THEMES) and name != THEMES + ".agterm-resource-manifest"}
    require(licensed and shipped, "Archive has no licensed theme inventory")
    unlicensed = sorted(set(shipped) - set(licensed))
    require(not unlicensed, f"Archive ships themes without a resolved author license: {unlicensed[:5]}")
    changed = sorted(name for name, content in shipped.items() if digest(content) != licensed[name])
    require(not changed, f"Archive themes differ from their licensed bytes: {changed[:5]}")


def release(source, arch, sha, version, output):
    """Write ASSET and return its evidence; OUTPUT must not already hold it."""
    data = stamp(source, version)
    evidence = validate_release(source, data, arch, sha, version)
    target = output / evidence["asset"]
    require(not target.exists(), f"{target} already exists")
    target.write_bytes(data)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="CI archive agterm-omarchy-ARCH.tar.gz")
    parser.add_argument("--arch", required=True, choices=sorted(promotion.ARCHITECTURES))
    parser.add_argument("--sha", required=True, help="full source commit recorded in the archive")
    parser.add_argument("--version", help="release version BASE.N; omitted means the BASE.0 rehearsal")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = upstream_base()
    version = args.version or f"{base}.0"
    release_tag(version, base, rehearsal=args.version is None)
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = release(args.archive.read_bytes(), args.arch, args.sha, version, args.output)
    (args.output / f"{evidence['asset']}.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, tarfile.TarError) as error:
        raise SystemExit(f"Release rejected: {error}") from error
