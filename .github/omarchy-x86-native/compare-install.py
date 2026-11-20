#!/usr/bin/env python3
"""Fail unless the installed payload is the extracted archive, byte for byte, plus the installer's own files.

Compares every entry's type, permission bits and content (or link target) without following links, so the
gates never launch bytes other than the ones the verdict names. Any unreadable entry is an error, not a pass.
"""

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys

# Written by scripts/install-linux.sh into the payload, and nothing else.
INSTALLER_FILES = {"share/agterm/RENDERER", "share/agterm/INSTALLED_FILES", "share/agterm/INSTALLED_DIRS"}


def raise_error(error):
    raise error


def entries(root):
    """Every entry under ROOT; a missing or non-directory root, or any traversal error, raises."""
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"{root} is not a directory")
    tree = {}
    for directory, names, files in os.walk(root, followlinks=False, onerror=raise_error):
        for name in names + files:
            path = Path(directory) / name
            info = os.lstat(path)
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                tree[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                tree[relative] = ("dir", stat.S_IMODE(info.st_mode))
            elif stat.S_ISREG(info.st_mode):
                tree[relative] = ("file", stat.S_IMODE(info.st_mode), hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                tree[relative] = ("special", info.st_mode)
    return tree


def compare(stage, installed):
    """Every difference between the trees that the installer does not account for."""
    expected, actual = entries(stage), entries(installed)
    problems = [f"special file {name}" for name, entry in {**expected, **actual}.items() if entry[0] == "special"]
    for name in sorted(expected.keys() | actual.keys()):
        if name not in actual:
            problems.append(f"missing {name}")
        elif name not in expected:
            if not (name in INSTALLER_FILES and actual[name][0] == "file"):
                problems.append(f"unexpected {name}")
        elif expected[name] != actual[name]:
            problems.append(f"changed {name}: {expected[name][:2]} -> {actual[name][:2]}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("installed", type=Path)
    args = parser.parse_args()
    try:
        problems = compare(args.stage, args.installed)
    except OSError as error:
        print(f"comparison failed: {error}")
        sys.exit(2)
    print("".join(f"{problem}\n" for problem in problems), end="")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
