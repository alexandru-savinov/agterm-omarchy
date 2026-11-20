#!/usr/bin/env python3
"""Verify the upstream workflow layout against the incorporated source commits."""

import argparse
import json
import re
import subprocess
from pathlib import Path

CI = ".github/workflows/ci.yml"
AUR = ".github/workflows/publish-aur.yml"
LEGACY_RELEASE = ".github/workflows/release-linux.yml"


def workflow_tree(commit):
    if not re.fullmatch(r"[0-9a-f]{40}|HEAD", commit):
        raise ValueError(f"invalid workflow source commit: {commit}")
    # Compare Git modes and blob IDs as well as paths, without normalizing checkout line endings.
    tree = subprocess.check_output(["git", "ls-tree", "-z", f"{commit}:.github/workflows"])
    entries = (entry.split(b"\t", 1) for entry in tree.split(b"\0") if entry)
    return {".github/workflows/" + name.decode(): entry for entry, name in entries
            if name.endswith((b".yml", b".yaml"))}


def check_layout(linux_port, upstream, local, pages):
    states = {}
    for page in pages:
        for item in page["workflows"]:
            path = item["path"]
            if path in states:
                raise ValueError(f"duplicate workflow in registry: {path}")
            states[path] = item["state"]
    errors = []
    if not linux_port.get(CI, b"").startswith(b"100644 blob "):
        errors.append(f"linux-port must ship {CI} as a regular workflow file")
    elif local.get(CI) != linux_port[CI]:
        errors.append(f"{CI} must be byte-identical to the pinned linux-port copy")
    if states.get(CI) != "disabled_manually":
        errors.append(f"{CI} must be disabled manually (registry state: {states.get(CI, 'missing')})")
    # The old downstream release workflow has its own fork skip gates and is protected by the
    # promotion tests. AUR is upstream publishing and does not belong in the Omarchy workflow tree.
    if AUR in local or states.get(AUR) == "active":
        errors.append(f"{AUR} must remain absent here")
    for path in sorted((linux_port.keys() | upstream.keys()) - {CI, AUR, LEGACY_RELEASE}):
        errors.append(f"new upstream workflow requires an explicit disposition: {path}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("linux_port")
    parser.add_argument("upstream")
    parser.add_argument("registry", type=Path)
    args = parser.parse_args()
    errors = check_layout(workflow_tree(args.linux_port), workflow_tree(args.upstream), workflow_tree("HEAD"),
                          json.loads(args.registry.read_text()))
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"{CI} matches linux-port at {args.linux_port} and is disabled")


if __name__ == "__main__":
    main()
