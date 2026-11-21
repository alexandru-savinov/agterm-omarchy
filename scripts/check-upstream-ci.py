#!/usr/bin/env python3
"""Verify the upstream workflow layout against the incorporated source commits."""

import argparse
import json
import re
import subprocess
from pathlib import Path

INHERITED = frozenset(f".github/workflows/{name}" for name in ("ci.yml", "release-linux.yml", "publish-aur.yml"))


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
    for path in sorted(INHERITED):
        if not linux_port.get(path, b"").startswith(b"100644 blob "):
            errors.append(f"linux-port must ship {path} as a regular workflow file")
        elif local.get(path) != linux_port[path]:
            errors.append(f"{path} must be byte-identical to the pinned linux-port copy")
        if states.get(path) != "disabled_manually":
            errors.append(f"{path} must be disabled manually (registry state: {states.get(path, 'missing')})")
    for path in sorted((linux_port.keys() | upstream.keys()) - INHERITED):
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
    for path in sorted(INHERITED):
        print(f"{path} matches linux-port at {args.linux_port} and is disabled")


if __name__ == "__main__":
    main()
