#!/usr/bin/env python3
"""Build the static landing page for agterm for Omarchy."""
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shutil

REPOSITORY = "alexandru-savinov/agterm-omarchy"
BASE = "https://github.com/" + REPOSITORY
STATUS_ISSUE = 525
README = Path(__file__).resolve().parents[2] / "README.md"
# Captured on an aarch64 Omarchy VM; the wording matches the launch posts, fact-checked against the pixels.
MEDIA = ("hero-tokyo-night.png", "theme-switch.mp4", "theme-gruvbox.png", "theme-catppuccin-latte.png")


def install_block(readme=None):
    """The first sh block in README's Install section, so the page never carries a second copy."""
    section = re.search(r"^## Install\n(.*?)(?=^## |\Z)", (readme or README).read_text(), re.MULTILINE | re.DOTALL)
    match = section and re.search(r"^```sh\n(.*?)^```$", section[1], re.MULTILINE | re.DOTALL)
    if not match or not re.search(r"^\s*gh attestation verify ", match[1], re.MULTILINE):
        raise ValueError("README.md has no verified install block under ## Install")
    return match[1].rstrip("\n")


def figure(name, alt, caption, width, height, extra=""):
    return (f'<figure class="shot{extra}"><img src="media/{name}" width="{width}" height="{height}" alt="{escape(alt)}" '
            f'loading="lazy" decoding="async"><figcaption>{caption}</figcaption></figure>')


def showcase():
    """Hero shot, theme clip, features and install: the part of the page people look at first."""
    hero = figure("hero-tokyo-night.png",
                  "Omarchy desktop with the Tokyo Night theme. Left: agterm with a sidebar of three workspaces (website, "
                  "api, infra) and six sessions, three with agent-status dots, showing a React component and a git log. "
                  "Upper right: btop's CPU view in Foot. Lower right: fastfetch in a second Foot window. Both terminals "
                  "use the Tokyo Night palette.",
                  "agterm next to Foot, Omarchy's terminal, in Tokyo Night. aarch64 VM with software rendering. "
                  "The workspace shows a demo project, and the status dots were set with agtermctl.", 1920, 1200, " wide")
    hero = hero.replace(' loading="lazy"', ' fetchpriority="high"')
    stills = (figure("theme-gruvbox.png", "Omarchy in Gruvbox, with warm dark terminals, a forest wallpaper, agterm on "
                     "the left, and btop and fastfetch on the right.", "Gruvbox", 1920, 1200)
              + figure("theme-catppuccin-latte.png", "Omarchy in Catppuccin Latte, with light terminals, a pastel "
                       "wallpaper, and the same three-window layout.", "Catppuccin Latte", 1920, 1200))
    return f'''<section class="showcase" aria-label="agterm on Omarchy">{hero}</section>
<section id="themes" aria-labelledby="themes-title"><p class="eyebrow">Theme following</p><h2 id="themes-title">It follows your Omarchy theme</h2>
<div class="clip"><figure class="shot"><video controls muted playsinline preload="metadata" poster="media/hero-tokyo-night.png" width="1920" height="1200" aria-label="Screen recording: omarchy theme set switches the desktop through Gruvbox, Catppuccin and Catppuccin Latte and back to Tokyo Night while agterm stays open."><source src="media/theme-switch.mp4" type="video/mp4"></video>
<figcaption><code>omarchy theme set</code> recolours the open agterm and Foot windows and updates the Omarchy bar and wallpaper: Gruvbox, Catppuccin, Catppuccin Latte, back to Tokyo Night. agterm stays open throughout. Theme-following demo on an aarch64 VM with software rendering and the optional Omarchy integration enabled. <a href="media/theme-switch.mp4">Download the recording (MP4)</a>.</figcaption></figure>
<div class="pair">{stills}<p class="muted">Same layout in Gruvbox and Catppuccin Latte. aarch64 VM, software rendering.</p></div></div></section>
<section id="features" aria-labelledby="features-title"><p class="eyebrow">On Omarchy</p><h2 id="features-title">What it does</h2>
<div class="cards features"><div><h3>Out of the box</h3><p>With the <strong>Follow Omarchy</strong> setting, on by default, <code>omarchy theme set</code>, <code>omarchy font set</code> and the text size apply without a restart, with Omarchy's padding and steady cursor. Shift+Enter and Alt+Shift+Enter send the same CSI-u bytes as Omarchy's Ghostty.</p></div>
<div><h3>Opt-in integration</h3><p>With Omarchy's Lua-based Hyprland configuration, <code>agtermctl integration omarchy enable</code> makes it the default terminal: Super+Return, <code>omarchy-launch-tui</code>, the floating terminal and the menu's Web App installer open in it. Super+C/V copy and paste, with Omarchy's terminal opacity and touchpad scroll factor. <code>agtermctl integration omarchy disable</code> removes the marked block from <code>hyprland.lua</code> and restores <code>xdg-terminals.list</code>, keeping any edits you made to either.</p></div>
<div><h3>Workspaces and control</h3><p>A libghostty terminal with a sidebar of workspaces and sessions, Live sessions that reattach when you reopen agterm, and <code>agtermctl</code> to drive it from scripts or coding agents.</p></div></div>
<div class="gaps"><h3>Known gaps</h3><p>GPU rendering and real touchpad feel are untested (the test VM uses software rendering); Omarchy's Default-terminal menu doesn't list agterm; "new terminal here" picks the newest session's folder in a multi-session window; the quick terminal has no global hotkey under Hyprland.</p></div></section>
<section id="install-steps" aria-labelledby="install-title"><p class="eyebrow">Install</p><h2 id="install-title">Verify, then install into <code>~/.local</code></h2>
<p>This block from the <a href="{BASE}#install">README</a> uses <code>gh</code> and <code>git</code>. It checks the release's checksum and build attestation before it extracts or installs anything into <code>~/.local</code>. agterm needs GTK4, libadwaita, WebKitGTK 6 and platform system libraries; the installer needs <code>python3</code>. To uninstall, run <code>agterm-release/agterm-omarchy/scripts/install-linux.sh --uninstall</code> from the directory where you ran the block.</p>
<pre><code>{escape(install_block())}</code></pre></section>'''


def release_status():
    """How releases are cut and gated; links to the live release list instead of naming a version."""
    return f'''<section class="readiness" id="releases" aria-labelledby="releases-title">
<div><p class="eyebrow">Releases</p><h2 id="releases-title">Released automatically from main.</h2><a class="button" href="{BASE}/releases/latest">Latest release →</a></div>
<div><p>Each <code>omarchy-v</code> release carries an x86_64 and an aarch64 archive, <code>SHA256SUMS</code>, <code>promotion.json</code> and GitHub build-provenance attestations. The automatic release path publishes only after Omarchy CI passes on a <code>main</code> push and the native gates pass on both architectures for those CI archives. Promotion then writes version-stamped release archives with different digests; <code>promotion.json</code> records both digests, and an automatic release also records <code>auto_release_run_id</code> in <code>promotion.json</code>. A versioned release dispatched by hand does not require native gate results.</p>
<p>The native gates install each archive in an Arch Linux (x86_64) or Arch Linux ARM (aarch64) container with the Omarchy desktop packages from the official Arch repositories and run ordinary-session, Live-restore, configuration and uninstall checks under Hyprland with Mesa software rendering. They do not test an Omarchy ISO installation or hardware GPU rendering.</p>
<p>For automatic releases, the gates run again on the published release archives right away and every two hours for 24 hours. A confirmed failure marks the release as a prerelease and opens a <code>release-incident</code> issue that pauses automatic releases.</p>
<p><a href="{BASE}#install">Install and verify</a> · <a href="{BASE}/releases">All releases</a> · <a href="{BASE}/blob/main/packaging/linux/prerelease-omarchy.md">Release mechanics</a></p></div></section>'''


UPSTREAM = "melonamin/agterm-linux"
UPSTREAM_BRANCH = "linux-port"
DRIFT_WORKFLOW = f"{BASE}/actions/workflows/upstream-drift.yml"
DRIFT_MAX_AGE_DAYS = 8  # the drift workflow runs weekly; one missed run makes the snapshot stale


def latest_drift_run(runs):
    """Newest run from `gh run list --json databaseId,createdAt,event,headBranch` that measured main.

    `--branch main` alone also matches a pull request from a fork's branch named main, whose artifact
    the fork controls, so only scheduled and dispatched runs count."""
    trusted = [run for run in runs if isinstance(run, dict) and run.get("headBranch") == "main"
               and run.get("event") in ("schedule", "workflow_dispatch")] if isinstance(runs, list) else []
    return max(trusted, key=lambda run: str(run.get("createdAt")), default=None)


def drift_snapshot(run, drift, now):
    """Validate the latest upstream-drift run and its artifact (#380, B4).

    `run` is the latest_drift_run() entry (or None), `drift` the
    parsed upstream-drift.json (or None). Returns a dict whose `status` is "loaded", "stale" or
    "unavailable"; an unavailable snapshot carries only a `reason`, never partial numbers."""
    def unavailable(reason, run_url=None):
        return {"status": "unavailable", "reason": reason, "workflow_url": DRIFT_WORKFLOW, "run_url": run_url}
    if not isinstance(run, dict):
        return unavailable("no successful upstream-drift run on main was found")
    run_id, created, measured = run.get("databaseId"), run.get("createdAt"), None
    if type(run_id) is int and run_id > 0 and isinstance(created, str):
        try:
            measured = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            pass
    if measured is None or measured.tzinfo is None:
        return unavailable("the latest upstream-drift run has no usable id or timestamp")
    run_url = f"{BASE}/actions/runs/{run_id}"  # constructed, not copied from the API response
    if not isinstance(drift, dict):
        return unavailable("the upstream-drift artifact is missing, expired or unreadable", run_url)
    counts = {key: drift.get(key) for key in ("ahead", "behind", "conflict_count")}
    commits = {key: drift.get(key) for key in ("main", "upstream")}
    if (any(type(value) is not int or value < 0 for value in counts.values())
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)
                   for value in commits.values())):
        return unavailable("the upstream-drift artifact does not have the expected fields", run_url)
    age_seconds = (now - measured).total_seconds()
    if age_seconds < -300:
        return unavailable("the upstream-drift run is dated in the future", run_url)
    age_days = max(age_seconds, 0) / 86400
    stale = age_days > DRIFT_MAX_AGE_DAYS
    return {"status": "stale" if stale else "loaded", "run_url": run_url, "workflow_url": DRIFT_WORKFLOW,
            "measured_at": measured.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "age_days": round(age_days, 1), "max_age_days": DRIFT_MAX_AGE_DAYS, **counts, **commits,
            "reason": (f"the latest measurement is {age_days:.1f} days old, older than {DRIFT_MAX_AGE_DAYS} days"
                       if stale else None)}


def drift_section(snapshot):
    """Render the drift snapshot; a missing or stale measurement is stated, never left blank."""
    if snapshot is None:
        return ""
    upstream = (f'<a href="https://github.com/{UPSTREAM}/tree/{UPSTREAM_BRANCH}">'
                f'<code>{UPSTREAM}:{UPSTREAM_BRANCH}</code></a>')
    run_url = snapshot.get("run_url")
    run_link = f'<a href="{run_url}">run {run_url.rsplit("/", 1)[1]}</a>' if run_url else None
    head = ('<section class="readiness" id="upstream-drift" aria-labelledby="upstream-drift-title">'
            '<div><p class="eyebrow">Upstream drift</p><h2 id="upstream-drift-title">Distance from linux-port</h2>'
            f'<a href="{DRIFT_WORKFLOW}">Weekly drift workflow →</a></div><div>')
    if snapshot["status"] == "unavailable":
        return (head + '<p class="drift-notice"><strong>Drift snapshot unavailable:</strong> '
                f'{escape(snapshot["reason"])}.' + (f' Latest run: {run_link}.' if run_link else '')
                + f' The distance from {upstream} is unknown for this build.</p></div></section>')
    notice = (f'<p class="drift-notice"><strong>Stale drift snapshot:</strong> {escape(snapshot["reason"])}. '
              'The weekly run may have failed; treat these numbers as historical.</p>'
              if snapshot["status"] == "stale" else "")
    return (head + notice
            + f'<p><code>main</code> is <strong>{snapshot["ahead"]} commits ahead</strong> and '
            f'<strong>{snapshot["behind"]} behind</strong> {upstream}; a merge would conflict on '
            f'<strong>{snapshot["conflict_count"]} paths</strong>.</p>'
            f'<p class="muted">Measured <time datetime="{snapshot["measured_at"]}">{snapshot["measured_at"]}</time> '
            f'at <code>{snapshot["main"][:8]}</code> against <code>{snapshot["upstream"][:8]}</code> by {run_link}. '
            'The run is report-only and changes nothing.</p></div></section>')


def write_site(destination, commit, drift=None):
    """`drift`: a drift_snapshot() result, or None when no drift snapshot was requested."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise ValueError("commit must be a hexadecimal Git commit ID")
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    content = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; img-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="description" content="agterm for Omarchy: the Linux build of agterm for Omarchy and Arch Linux, with verified x86_64 and aarch64 releases.">
<title>agterm for Omarchy</title><link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="style.css"></head>
<body><a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="./">agterm <span>/ Omarchy</span></a><nav aria-label="Primary"><a href="#install-steps">Install</a><a href="{BASE}/releases">Releases ↗</a><a href="#help">Help</a><a href="{BASE}">Repository ↗</a></nav></header>
<main id="main"><section class="hero"><p class="eyebrow">GTK4/libadwaita build for Omarchy and Arch Linux</p>
<h1>agterm for Omarchy</h1>
<p class="lead">agterm is a terminal for running many shells and coding agents side by side, grouped into workspaces and driven by a control API. This project builds and releases its Linux version for x86_64 and aarch64, based on <a href="https://github.com/umputun/agterm">umputun/agterm</a> and <a href="https://github.com/melonamin/agterm-linux">melonamin/agterm-linux</a>.</p>
<div class="actions"><a class="button" href="#install-steps">Install</a><a href="{BASE}/releases/latest">Latest release →</a></div></section>
{showcase()}
{release_status()}
<section id="help" class="readiness" aria-labelledby="help-title"><div><p class="eyebrow">Status and help</p><h2 id="help-title">Where to ask and what is next</h2></div><div><p>The pinned <a href="{BASE}/issues/{STATUS_ISSUE}">Status and roadmap</a> issue says what is being worked on. Ask questions and share ideas in <a href="{BASE}/discussions">Discussions</a>, report a bug through the <a href="{BASE}/issues/new?template=bug.yml">bug form</a>, and read <a href="{BASE}/blob/main/CONTRIBUTING.md">CONTRIBUTING.md</a> to send a fix. Report suspected vulnerabilities privately through <a href="{BASE}/blob/main/SECURITY.md">SECURITY.md</a>.</p></div></section>
{drift_section(drift)}
<section class="evidence" aria-labelledby="evidence-title"><p class="eyebrow">Proof, not promises</p><h2 id="evidence-title">Inspect the builds</h2><div class="cards"><a href="{BASE}/actions/workflows/omarchy-ci.yml"><h3>Builds and checks ↗</h3><p>Results of the automated tests on both architectures, with logs and downloadable builds. Check the commit and architecture.</p></a><a href="{BASE}/releases"><h3>Releases ↗</h3><p>Every release with its archives, <code>SHA256SUMS</code> and <code>promotion.json</code>.</p></a></div></section>
</main><footer><p>Built from <a href="{BASE}/commit/{commit}">{commit[:8]}</a> · <a href="status.json">Machine-readable snapshot</a></p><p>Based on <a href="https://github.com/melonamin/agterm-linux">melonamin/agterm-linux</a> and <a href="https://github.com/umputun/agterm">upstream agterm</a>. This site documents the Omarchy port.</p></footer></body></html>
'''
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.html").write_text(content)
    # The Team room was removed; keep its old URL working.
    (destination / "team.html").write_text(
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=./">'
        '<title>agterm for Omarchy</title><link rel="canonical" href="./"></head>'
        '<body><p>This page has moved to <a href="./">agterm for Omarchy</a>.</p></body></html>\n')
    for asset in ("style.css", "favicon.svg"):
        shutil.copyfile(Path(__file__).with_name(asset), destination / asset)
    (destination / "media").mkdir(exist_ok=True)
    for name in MEDIA:
        shutil.copyfile(Path(__file__).with_name("media") / name, destination / "media" / name)
    (destination / ".nojekyll").touch()
    (destination / "status.json").write_text(json.dumps({
        "repository": REPOSITORY, "readiness": "released", "releases": f"{BASE}/releases", "upstream_baseline": "v0.43.0",
        "status_issue": f"{BASE}/issues/{STATUS_ISSUE}", "commit": commit, "generated_at": generated,
        # Kept empty for readers of the old snapshot; the task list, roster and queue overlay were removed.
        "tasks": [], "roster": {"observed_at": None, "agents": []}, "queue": None, "queue_status": "not-requested",
        # "not-requested", "loaded", "stale" (older than DRIFT_MAX_AGE_DAYS; numbers kept, flagged) or "unavailable".
        "upstream_drift": drift,
        "upstream_drift_status": "not-requested" if drift is None else drift["status"],
    }, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--drift-run", type=Path,
                        help="`gh run list --json databaseId,createdAt,event,headBranch` output for "
                             "upstream-drift.yml (optional)")
    parser.add_argument("--drift", type=Path, help="upstream-drift.json from that run's artifact")
    args = parser.parse_args()
    drift = None
    if args.drift_run is not None or args.drift is not None:
        # Either input may be missing (the fetch step is continue-on-error); drift_snapshot says which.
        def load(path):
            try:
                return json.loads(path.read_text()) if path is not None else None
            except (OSError, ValueError):
                return None
        drift = drift_snapshot(latest_drift_run(load(args.drift_run)), load(args.drift), datetime.now(timezone.utc))
        if drift["status"] != "loaded":
            print(f"warning: upstream drift {drift['status']}: {drift['reason']}")
    write_site(args.output, args.commit, drift=drift)


if __name__ == "__main__":
    main()
