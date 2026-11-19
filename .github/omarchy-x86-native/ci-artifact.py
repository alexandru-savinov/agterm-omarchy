#!/usr/bin/env python3
"""Select a successful main Omarchy CI run and download one architecture's archive for the native gates.

The run, job, artifact and payload checks are the promotion's own (scripts/prepare-omarchy-prerelease.py,
release mode), so a run these gates pass is one a versioned release would accept. Unlike a promotion, the
commit only has to be on main, not its head, and nothing is checked against existing tags.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import zipfile

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare-omarchy-prerelease.py"


def load_promotion():
    spec = importlib.util.spec_from_file_location("promotion", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


promotion = load_promotion()
require = promotion.require


def select_run(run_id):
    """The requested run, or with no RUN_ID the newest main run a versioned release accepts."""
    if run_id:
        require(run_id.isdigit() and int(run_id) > 0, f"Invalid run ID {run_id!r}")
        run = promotion.api(f"actions/runs/{run_id}")
        require(run["id"] == int(run_id), "Requested run ID mismatch")
    else:
        run = promotion.discover_releasable()
        require(run is not None, "No releasable main CI run")
    return run


def validate(run):
    """The run's artifacts by architecture, or ValueError when release mode would reject the run."""
    require(run["head_branch"] == "main", "The gates take only main CI runs")
    sha = run["head_sha"]
    promotion.validate_run(run, promotion.api("actions/workflows/ci.yml"), sha, sha)
    require(promotion.on_main(sha), f"{sha} is not a commit on main")
    promotion.validate_jobs(promotion.run_jobs(run), release=True)
    return promotion.select_artifacts(promotion.run_artifacts(run), run, sha)


def resolve(args):
    run = select_run(args.run_id)
    validate(run)
    print(run["id"], run["run_attempt"], run["head_sha"])


def download(args):
    run = select_run(args.run_id)
    require(run["run_attempt"] == args.attempt, "Run attempt changed since it was resolved")
    artifact = validate(run)[args.arch]
    name, data = promotion.download_artifact(artifact, args.arch, run["head_sha"])
    current = promotion.api(f"actions/runs/{run['id']}")
    require(current["run_attempt"] == run["run_attempt"] and current["status"] == "completed"
            and current["conclusion"] == "success", "Run changed while downloading")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / name).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    source = {"repository": promotion.REPOSITORY, "source_commit": run["head_sha"], "source_branch": "main",
              "source_run": run["html_url"], "source_run_attempt": run["run_attempt"],
              "source_workflow": promotion.WORKFLOW,
              "artifacts": [{"id": artifact["id"], "name": artifact["name"],
                             "transport_digest": artifact["digest"], "filename": name, "sha256": digest}]}
    (args.output / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    source_digest = hashlib.sha256((args.output / "source.json").read_bytes()).hexdigest()
    (args.output / "SHA256SUMS").write_text(f"{digest}  {name}\n{source_digest}  source.json\n")
    print(name, digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve_parser = commands.add_parser("resolve", help="validate a run; print its id, attempt and commit")
    resolve_parser.add_argument("--run-id", default="")
    download_parser = commands.add_parser("download", help="download one architecture's validated archive")
    download_parser.add_argument("--run-id", required=True)
    download_parser.add_argument("--attempt", type=int, required=True)
    download_parser.add_argument("--arch", choices=sorted(promotion.ARCHITECTURES), required=True)
    download_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (resolve if args.command == "resolve" else download)(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(f"CI artifact rejected: {error}") from error
