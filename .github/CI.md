# Omarchy CI

`.github/workflows/ci.yml` validates pull requests targeting `main` or `omarchy`, pushes to `main`, and manual runs.
Pull request builds test GitHub's merge commit; trusted artifact promotion still requires a successful push or manual run at the exact branch head.

The **Installer and scripts** job discovers every `scripts/tests/test_*.py` module with:

```sh
python3 -I -m unittest discover -s scripts/tests -p 'test_*.py' -v
```

The job explicitly installs ripgrep, which the real core-boundary fixtures require.
New helper regression modules join this gate without a workflow allowlist change.
The suite covers installer/staging behavior, packaging and version handling, core boundaries, cleanup/identity records, zmx cache policy, and promotion preflight.
The separate Zig transport fixture remains a shell test.
The syntax/ShellCheck step uses one shared file list for both checks, including the changed setup, package, cache-verification, resource-test, and core-boundary helpers.
Add new Linux shell helpers to that list when introducing them.
Only `setup-linux.sh` excludes ShellCheck SC2030/SC2031: its Ghostty and zmx subshells each explicitly assign all cache environment variables.
The shared-parent-value warning does not apply to those independent assignments; all other diagnostics and files retain the normal gate.

The lightweight job has no compiled zmx, so its two real-zmx identity cases are explicitly skipped.
Both architecture build jobs rerun `test_omarchy_identity.py` after verifying the built zmx cache, with `AGTERM_IDENTITY_TEST_ZMX` set to that exact executable.
An executable check prevents a missing helper from silently skipping this gate.
Those tests use fresh temporary socket directories and clean up their own sentinel daemons; they do not use a user's default zmx endpoint.
This closes the real-helper coverage gap without requiring a graphical application in the scripts job.

The existing aggregate CI gate still requires these jobs to succeed.
Hosted checks and archives do not establish native Omarchy/Wayland acceptance for the produced payload.

The core boundary guard keeps `ConfigPathsTests.swift` identical to the upstream base by default.
Its single reviewed exception in `packaging/linux/core-fixture-exceptions.tsv` pins the exact path,
upstream Git blob, accepted fixture Git blob and task rationale; wildcards, additional records,
malformed fields, different bases and any further content change fail.
The file must remain regular and non-executable, and the host import/API checks still apply.
This exception covers [#343](https://github.com/alexandru-savinov/agterm-omarchy/issues/343):
a portable test fixture restores its editor environment after global zsh startup while preserving
the product command, private login/interactive rc sourcing and every existing assertion.
The accepted bytes are the reviewed `c5262fe4` fixture; this is not an exemption for other core changes.
Remove the exception when the upstream base includes the fixture correction.
Any replacement pair needs an explicit review and rationale rather than updating hashes to silence a failure.
