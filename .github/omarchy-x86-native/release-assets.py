#!/usr/bin/env python3
"""Resolve a published Omarchy release's assets to immutable IDs and digests once, and download them by ID.

A tag's assets can be replaced; an asset ID's bytes are pinned here by the digest resolved with it, and every
download is checked against that digest, so each gate leg tests what the source job resolved.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = "alexandru-savinov/agterm-omarchy"
TAG = re.compile(r"omarchy-(?:dev-[0-9a-f]{12}-run[0-9]+|v(?P<version>[0-9]+(?:\.[0-9]+)+))")
ARCHITECTURES = ("x86_64", "aarch64")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/{path}"]))


def asset_bytes(asset_id):
    return subprocess.check_output(["gh", "api", "-H", "Accept: application/octet-stream",
                                    f"repos/{REPOSITORY}/releases/assets/{int(asset_id)}"])


def fetch(asset_id, sha256):
    data = asset_bytes(asset_id)
    require(hashlib.sha256(data).hexdigest() == sha256, f"Asset {asset_id} does not match its resolved digest")
    return data


def archive_name(tag, arch):
    version = TAG.fullmatch(tag)["version"]
    return f"agterm-omarchy-{version}-{arch}.tar.gz" if version else f"agterm-omarchy-{arch}.tar.gz"


def resolve(tag, arches):
    require(TAG.fullmatch(tag), f"Unexpected tag {tag!r}")
    require(arches and set(arches) <= set(ARCHITECTURES), f"Unexpected architectures {arches}")
    release = api(f"releases/tags/{tag}")
    require(release["tag_name"] == tag and not release["draft"], f"{tag} is not a published release")
    assets = {}
    for name in ["SHA256SUMS", "promotion.json"] + [archive_name(tag, arch) for arch in arches]:
        matches = [item for item in release["assets"] if item["name"] == name]
        require(len(matches) == 1, f"Missing unique asset {name}")
        digest = matches[0].get("digest") or ""
        require(matches[0].get("state") == "uploaded" and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
                f"{name} has no recorded sha256 digest")
        assets[name] = {"id": matches[0]["id"], "name": name, "sha256": digest.removeprefix("sha256:")}
    sums = {}
    for line in fetch(assets["SHA256SUMS"]["id"], assets["SHA256SUMS"]["sha256"]).decode().splitlines():
        digest, _, name = line.partition("  ")
        require(re.fullmatch(r"[0-9a-f]{64}", digest) and name and name not in sums, f"Bad SHA256SUMS line {line!r}")
        sums[name] = digest
    for name, asset in assets.items():
        if name != "SHA256SUMS":
            require(sums.get(name) == asset["sha256"], f"SHA256SUMS does not list {name} at its asset digest")
    promotion = json.loads(fetch(assets["promotion.json"]["id"], assets["promotion.json"]["sha256"]))
    sha = promotion.get("source_commit", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "promotion.json has no source commit")
    return {"tag": tag, "sha": sha, "sums": assets["SHA256SUMS"], "promotion": assets["promotion.json"],
            "archives": {arch: assets[archive_name(tag, arch)] for arch in arches}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve_parser = commands.add_parser("resolve", help="print the release's resolved assets as JSON")
    resolve_parser.add_argument("--tag", required=True)
    resolve_parser.add_argument("--arch", action="append", required=True)
    download_parser = commands.add_parser("download", help="download one asset by ID, checked against its digest")
    download_parser.add_argument("--asset-id", type=int, required=True)
    download_parser.add_argument("--sha256", required=True)
    download_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "resolve":
        print(json.dumps(resolve(args.tag, args.arch), sort_keys=True))
    else:
        args.output.write_bytes(fetch(args.asset_id, args.sha256))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError) as error:
        raise SystemExit(f"Release asset rejected: {error}") from error
