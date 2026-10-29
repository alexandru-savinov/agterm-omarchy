#!/usr/bin/env python3
"""Verify a disposable Actions probe; never log or persist its value."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re


def verify(value, expected, channel):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", channel):
        raise ValueError("missing or invalid channel variable")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("missing or invalid public SHA256 variable")
    if not re.fullmatch(r"agterm-probe-[0-9a-f]{64}", value):
        raise ValueError("missing or invalid disposable probe secret")
    actual = hashlib.sha256(value.encode()).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("probe SHA256 comparison failed")
    return {"passed": True, "channel": channel, "expected_sha256": expected,
            "secret_delivery_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/agent-channel/result.json"))
    args = parser.parse_args()
    try:
        result = verify(os.environ.get("AGTERM_PROBE_VALUE", ""),
                        os.environ.get("AGTERM_PROBE_SHA256", ""),
                        os.environ.get("AGTERM_PROBE_CHANNEL", ""))
    except ValueError as error:
        result = {"passed": False, "reason": str(error), "secret_delivery_verified": False}
    result.update(repository=os.environ.get("GITHUB_REPOSITORY"),
                  source_commit=os.environ.get("GITHUB_SHA"), run_id=os.environ.get("GITHUB_RUN_ID"),
                  run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"), event=os.environ.get("GITHUB_EVENT_NAME"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    message = "PASS: repository configuration and disposable secret delivery verified" if result["passed"] else "FAIL: " + result["reason"]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("## Agent channel verification\n\n" + message + "\n\n")
            summary.write("The result artifact contains the public digest and run identity, never the probe value.\n")
    print(message)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
