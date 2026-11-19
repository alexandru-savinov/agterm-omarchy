#!/usr/bin/env python3
"""Print the uninstall gate's row: PASS only if the uninstaller succeeded, the prefix is empty or absent,
and no agterm or zmx process is left. A failed inspection is a FAIL, never an empty result."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

# The gate's own processes (this script, gates.sh) carry the gate directory in their command lines.
GATE_DIRECTORY = "omarchy-x86-native"


def raise_error(error):
    raise error


def leftovers(prefix):
    """Every entry left under PREFIX; an absent prefix has none."""
    if not os.path.lexists(prefix):
        return []
    if prefix.is_symlink() or not prefix.is_dir():
        return [f"prefix {prefix} is not a directory"]
    entries = []
    for directory, names, files in os.walk(prefix, onerror=raise_error):
        entries += [str((Path(directory) / name).relative_to(prefix)) for name in names + files]
    return sorted(entries)


def strays(user):
    """agterm or zmx processes of USER; pgrep's 1 means none, and any other status is an error."""
    result = subprocess.run(["pgrep", "-u", user, "-a", "-f", "agterm|zmx"], capture_output=True, text=True)
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        raise OSError(f"pgrep exited {result.returncode}: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if GATE_DIRECTORY not in line]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--uninstall-status", type=int, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        left, stray = leftovers(args.prefix), strays(args.user)
    except OSError as error:
        print(f"FAIL exit={args.uninstall_status} inspection error: {error}")
        sys.exit(1)
    (args.evidence / "uninstall-left.txt").write_text("".join(f"{entry}\n" for entry in left))
    (args.evidence / "uninstall-stray.txt").write_text("".join(f"{line}\n" for line in stray))
    ok = args.uninstall_status == 0 and not left and not stray
    print(f"{'PASS' if ok else 'FAIL'} exit={args.uninstall_status} files_left={len(left)} stray_processes={len(stray)}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
