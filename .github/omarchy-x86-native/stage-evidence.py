#!/usr/bin/env python3
"""Copy the container's evidence into a fresh host-owned directory: regular files and directories only.

The container writes /out as the account that ran the tested payload. A symlink there would make the host's
uploader publish whatever runner file it points at, so links and special files are never followed or copied:
each is listed in rejected.txt and the staging fails.
"""

import argparse
import os
from pathlib import Path
import stat
import sys


def raise_error(error):
    raise error


def stage(source, target):
    """Rejected relative paths; regular files and directories under SOURCE are copied into a new TARGET.

    A scan error raises: evidence that could not be read is never silently left out."""
    if not stat.S_ISDIR(os.lstat(source).st_mode):
        raise NotADirectoryError(f"{source} is not a directory")
    target.mkdir(mode=0o755)
    rejected = []
    for directory, names, files in os.walk(source, followlinks=False, onerror=raise_error):
        relative = Path(directory).relative_to(source)
        for name in sorted(names):
            path = Path(directory) / name
            if stat.S_ISDIR(os.lstat(path).st_mode):
                (target / relative / name).mkdir(mode=0o755)
            else:
                rejected.append(str(relative / name))
        names[:] = [name for name in names if stat.S_ISDIR(os.lstat(Path(directory) / name).st_mode)]
        for name in sorted(files):
            path = Path(directory) / name
            if not stat.S_ISREG(os.lstat(path).st_mode):
                rejected.append(str(relative / name))
                continue
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as reader, open(target / relative / name, "xb") as writer:
                while chunk := reader.read(1 << 20):
                    writer.write(chunk)
    if rejected:
        (target / "rejected.txt").write_text("".join(f"{path}\n" for path in sorted(rejected)))
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    rejected = stage(args.source, args.target)
    for path in rejected:
        print(f"::error::evidence {path} is not a regular file or directory; not copied")
    sys.exit(1 if rejected else 0)


if __name__ == "__main__":
    main()
