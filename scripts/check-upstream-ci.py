#!/usr/bin/env python3
"""Verify the upstream workflow layout against the incorporated source commits."""

import argparse
import json
import os
import re
import subprocess

INHERITED = frozenset(f".github/workflows/{name}" for name in ("ci.yml", "release-linux.yml", "publish-aur.yml"))
ROOT_KEY = re.compile(r"""^(?P<key>[a-zA-Z][a-zA-Z0-9_-]*|'on'|"on")\s*:(?P<value>.*)$""")
CALL_KEY = re.compile(r"""^(?:workflow_call|'workflow_call'|"workflow_call")\s*:\s*(?:\{\}\s*)?(?:#.*)?$""")


def workflow_tree(commit):
    if not re.fullmatch(r"[0-9a-f]{40}|HEAD", commit):
        raise ValueError(f"invalid workflow source commit: {commit}")
    # Compare Git modes and blob IDs as well as paths, without normalizing checkout line endings.
    tree = subprocess.check_output(["git", "ls-tree", "-z", f"{commit}:.github/workflows"])
    entries = (entry.split(b"\t", 1) for entry in tree.split(b"\0") if entry)
    return {".github/workflows/" + name.decode(): entry for entry, name in entries
            if name.endswith((b".yml", b".yaml"))}


def workflow_call_only(source):
    """Recognize an unambiguous reusable-only trigger. Any unsupported YAML form fails closed."""
    entries = []
    events = []
    in_events = False
    for line in source.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            if in_events:
                if "\t" in line[:len(line) - len(line.lstrip())]:
                    return False
                events.append(line)
            continue
        key = ROOT_KEY.fullmatch(line)
        if key is None:
            # Explicit keys, aliases, merges and multiple documents are outside the accepted form.
            return False
        in_events = key["key"].strip("'\"") == "on"
        if in_events:
            entries.append(key["value"].split("#", 1)[0].strip())
    if entries in (["workflow_call"], ["[workflow_call]"]):
        return not events
    if entries != [""] or not events:
        return False
    indent = len(events[0]) - len(events[0].lstrip(" "))
    if not CALL_KEY.fullmatch(events[0][indent:]):
        return False
    if "{}" in events[0].split("#", 1)[0]:
        return len(events) == 1
    return all(len(line) - len(line.lstrip(" ")) > indent for line in events[1:])


def registry_state(repository, path):
    response = subprocess.run(
        ["gh", "api", "--include", f"repos/{repository}/actions/workflows/{path.rsplit('/', 1)[1]}"],
        capture_output=True, text=True,
    )
    headers, _, body = response.stdout.partition("\n\n")
    status = re.match(r"HTTP/[0-9.]+ ([0-9]{3})\b", headers)
    if status and status[1] == "404":
        return None
    if response.returncode or not status or status[1] != "200":
        raise ValueError(f"cannot verify registry state for {path}: {response.stderr.strip() or headers}")
    workflow = json.loads(body)
    if workflow["path"] != path or not isinstance(workflow["state"], str):
        raise ValueError(f"unexpected workflow in registry lookup for {path}")
    return workflow["state"]


def check_layout(linux_port, upstream, local, states, sources):
    errors = []
    for path in sorted(INHERITED):
        if not linux_port.get(path, b"").startswith(b"100644 blob "):
            errors.append(f"linux-port must ship {path} as a regular workflow file")
        elif local.get(path) != linux_port[path]:
            errors.append(f"{path} must be byte-identical to the pinned linux-port copy")
        state = states[path]
        if state is None and workflow_call_only(sources.get(path, "")):
            continue
        if state != "disabled_manually":
            errors.append(f"{path} must be disabled manually (registry state: {state or 'unregistered (HTTP 404)'})")
    for path in sorted((linux_port.keys() | upstream.keys()) - INHERITED):
        errors.append(f"new upstream workflow requires an explicit disposition: {path}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("linux_port")
    parser.add_argument("upstream")
    args = parser.parse_args()
    linux_port = workflow_tree(args.linux_port)
    states = {path: registry_state(os.environ["GITHUB_REPOSITORY"], path) for path in sorted(INHERITED)}
    sources = {path: subprocess.check_output(["git", "show", f"{args.linux_port}:{path}"], text=True)
               for path in INHERITED if path in linux_port and states[path] is None}
    errors = check_layout(linux_port, workflow_tree(args.upstream), workflow_tree("HEAD"), states, sources)
    if errors:
        raise SystemExit("\n".join(errors))
    for path in sorted(INHERITED):
        state = states[path] or "unregistered with workflow_call as its only trigger"
        print(f"{path} matches linux-port at {args.linux_port}: {state}")


if __name__ == "__main__":
    main()
