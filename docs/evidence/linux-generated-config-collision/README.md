# Shared runtime configuration collision

At `fd85fc5824a782d17b2aee0cb113ef31f9ce66ab`, two invocations of the exact Linux `writeTempConf` helper use the same filename when they share `XDG_RUNTIME_DIR`, even with different `AGTERM_STATE_DIR` values.
A deterministic two-process probe forces A-write → B-write/read → A-read.
A reads B's configuration in the shared-runtime case; each reads its own configuration with separate runtime directories.

This is evidence for [issue #124](https://github.com/alexandru-savinov/agterm-omarchy/issues/124), not a production fix.
The [original report](https://github.com/alexandru-savinov/agterm-omarchy/issues/124#issuecomment-6563958731) contains the same runner and source identities.
The accepted candidate and its [native acceptance conditions](https://github.com/alexandru-savinov/agterm-omarchy/pull/123) remain unchanged.

## Evidence and boundaries

| Case | Same returned path | A reads after B writes | Exit codes |
| --- | --- | --- | --- |
| Shared runtime, distinct state directories | Yes | B's `background = #445566` | Both 0 |
| Separate runtime, distinct state directories | No | A's `background = #112233` | Both 0 |

`report.json` records the original public-run result on Apple Swift 6.4, arm64 macOS.
All output paths in the report are relative to its disposable fixture directory.
`SHA256SUMS` binds this document, the runner and the report.

The runner extracts the private helper verbatim from the pinned Git object, compiles it in a Foundation-only wrapper, and uses pipes to order the writes and reads.
The wrapper's file read models the gap before `buildConfig` calls `ghostty_config_load_file`.
No GTK, libghostty parser, app payload, graphical session or VM ran.
This does not demonstrate visible rendering corruption or native Linux execution.
Adjacent fixed defaults/theme filenames were inspected in source but were not exercised by this probe.
Atomic file writes avoid partial content but do not serialize a cross-process write/load pair.

## Replay

Requirements: Git, Python 3, Swift with Foundation, and a checkout containing the pinned commit.
If needed, fetch the public `omarchy` branch into the checkout before replay.
Run from this evidence directory, using the Linux source checkout path as the final argument:

```sh
shasum -a 256 -c SHA256SUMS
probe_dir=$(mktemp -d)
cp probe.py "$probe_dir/probe.py"
python3 "$probe_dir/probe.py" /path/to/agterm-omarchy-checkout
```

Use a fresh empty directory for each run; do not run the fixture inside the evidence directory.
It compiles and writes only beside its copied script, assigns private state/runtime directories to its two probes, and refuses reused case directories.
Child probes are awaited, or killed and awaited on failure.
The extracted method, Swift wrapper, executable, module cache and generated report remain in that directory for inspection.
No installed application or existing session is touched.
Compiler identity can differ on another host; source, helper, wrapper and runner hashes should match the retained report.

## Implementation handoff

A production fix should give each app instance private configuration storage with an explicit lifetime through loading and reloads.
Changing `AGTERM_STATE_DIR` alone does not isolate the current generated files.
Preserve the desktop runtime environment and Wayland/D-Bus routing.
Retain the two-process regression and separate-runtime control when validating a fix.

Before changing cleanup, inspect conditional theme path reuse and shutdown ordering.
At the pinned commit, `GhosttyApp.shared` has no explicit shutdown/free path, while `App.main` calls `exit(status)` after `g_application_run`.
A deinitializer alone is therefore insufficient evidence of normal-exit cleanup.
Production implementation and release decisions remain with the Linux integration owner.

This is same-account agent evidence, not independent-account approval.
