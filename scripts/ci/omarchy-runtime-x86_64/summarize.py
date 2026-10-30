"""Turn the harness result files into a job summary, annotations and summary.json.

Only result.json/cleanup.json written by the unmodified harness count as pass/fail.
Checks after the first failure are reported NOT RUN, never passed.
"""
import json
import os
import sys
from pathlib import Path

ORDINARY = [
    "primary PTY execution and screen read-back",
    "native Wayland rendering: red-to-green PTY pixel change",
    "workspace/session creation and split PTY execution",
    "overlay output, dismissal, and restored split input",
    "WebKitGTK HTML overlay load",
    "split/session/workspace teardown",
    "no terminal GL-context or app CSS failures",
]
LIVE_EXTRA = [
    "Live mode shell execution through bundled zmx",
    "Live session preserves shell PID across application restart",
]
LABEL = ("hosted GitHub x86_64 runner, Arch Linux container, headless Wayland compositor "
         "(Hyprland), software rendering - not native Omarchy hardware")


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def expected(mode):
    return ORDINARY[:1] + LIVE_EXTRA + ORDINARY[1:] if mode == "live" else list(ORDINARY)


def evaluate(out, mode):
    result = load(out / f"harness-{mode}" / "result.json")
    cleanup = load(out / f"harness-{mode}" / "cleanup.json")
    done = []
    if result:
        done = result.get("checks") or result.get("completed_checks") or []
    passed = bool(result and result.get("passed"))
    rows = []
    failed_marked = False
    for name in expected(mode):
        if name in done:
            rows.append((name, "PASS"))
        elif not failed_marked and result is not None and not passed:
            rows.append((name, "FAIL"))
            failed_marked = True
        else:
            rows.append((name, "NOT RUN"))
    log_tail = read(out / f"harness-{mode}.log").splitlines()[-12:]
    return {
        "mode": mode,
        "harness_exit": read(out / f"harness-{mode}-exit.txt", "not run"),
        "result_json_present": result is not None,
        "passed": passed,
        "cleanup": cleanup,
        "checks": [{"check": n, "status": s} for n, s in rows],
        "log_tail": log_tail,
    }


def main():
    out = Path(sys.argv[1])
    modes = [evaluate(out, m) for m in ("none", "live")]
    summary = {
        "label": LABEL,
        "compositor_status": read(out / "compositor-status.txt", "unknown"),
        "zmx_probe_exit": read(out / "zmx-probe-exit.txt", "not run"),
        "container_exit": read(out / "container-exit.txt", "unknown"),
        "image": read(out / "image-digest.txt"),
        "packages": read(out / "packages.txt").splitlines(),
        "cpu_flags": read(out / "host-cpu-flags-summary.txt").splitlines(),
        "modes": modes,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    lines = [f"## Omarchy runtime x86_64\n\n**Environment:** {LABEL}\n",
             f"- Compositor: `{summary['compositor_status']}`",
             f"- Direct `zmx list` exit: `{summary['zmx_probe_exit']}` (132 = SIGILL)",
             f"- Container image: `{summary['image']}`",
             "- Packages: " + ", ".join(f"`{p}`" for p in summary["packages"]),
             "- Host CPU flags: " + ", ".join(summary["cpu_flags"]), ""]
    for m in modes:
        lines.append(f"### restore_mode={m['mode']}: {'PASS' if m['passed'] else 'FAIL'} "
                     f"(harness exit {m['harness_exit']}, cleanup {json.dumps(m['cleanup'])})\n")
        lines.append("| Check | Status |\n|---|---|")
        lines += [f"| {c['check']} | {c['status']} |" for c in m["checks"]]
        lines.append("\n<details><summary>harness log tail</summary>\n\n```\n" +
                     "\n".join(m["log_tail"]) + "\n```\n</details>\n")
    text = "\n".join(lines) + "\n"
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as handle:
            handle.write(text)
    for m in modes:
        title = f"harness restore_mode={m['mode']}"
        if m["passed"]:
            print(f"::notice title={title}::PASS {len(m['checks'])} checks ({LABEL})")
        else:
            first = next((c["check"] for c in m["checks"] if c["status"] == "FAIL"), "no result.json")
            tail = (m["log_tail"][-1] if m["log_tail"] else "").replace("\n", " ")[:300]
            print(f"::error title={title}::FAIL at '{first}': {tail}")
    if summary["zmx_probe_exit"] not in ("0", "not run"):
        print(f"::warning title=zmx probe::bundled zmx 'list' exited {summary['zmx_probe_exit']}")
    return 0 if all(m["passed"] for m in modes) else 1


if __name__ == "__main__":
    sys.exit(main())
