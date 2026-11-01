# Exact hosted ARM64 payload: native Omarchy acceptance

Root ran the unchanged ARM64 archive from [trusted run 41335760930](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/41335760930),
source `fd85fc5824a782d17b2aee0cb113ef31f9ce66ab`, on the existing Omarchy ARM64 VM.
Execution ran from 2026-11-01 19:20:42 to 19:20:54 UTC.
[The operator report](https://github.com/alexandru-savinov/agterm-omarchy/issues/52#issuecomment-6562708234)
and these records replace neither historical failures nor their original evidence.

| Gate | Result |
| --- | --- |
| Ordinary native Wayland | 7 checks passed, exit 0 |
| Native Live mode | 9 checks passed, exit 0; bundled zmx runs without the earlier SIGILL |
| Frontend restart | Shell PID 559232 persisted across the restart and was absent after cleanup |
| Harness cleanup | Passed, including the Live sentinel observed before and after daemon teardown, then confirmed killed and absent |
| Separate process audit | 24 ordinary and 34 Live process identities tracked; no remaining test processes, new app processes, or retained test state |
| Preservation | The protected existing session, installed payload, actual login shell and shared Ghostty runtime files were preserved |
| Payload identity | Running app image hashes match the candidate; extracted file bytes, modes and symlink targets remained unchanged |

[native-report.json](native-report.json) retains both mode results, execution identities and cleanup evidence.
The audit used no forced cleanup and observed no timeout or received signal.
The original hosted reconnect failure remains unclassified; its unchanged assertion passed in both the later diagnostic run and this trusted candidate build.

## Exact identities

- ARM64 archive SHA256: `e9aecad7c183ef2ee325ba8bc91b036a7dbf3c2f21dff8645e6ee330ef919671`.
- Bundled zmx SHA256: `0fdfe9d460eb0ee12691d466dd2248920ae67bf37309504a9d32da7feade6cdd`.
- Running app SHA256: `79d0a869081f2f0565754acf5cf9f72b8edf48545651e43c96dec921dd00acaa`.
- Unchanged harness SHA256: `9fe046d6fbbf0dbc79e0591b2fbe8869af4f79604d6a2f7a470c5750044cee40`.

[artifact-provenance.json](artifact-provenance.json) is the unchanged full validator's promotion manifest.
It binds both downloaded archives to the trusted source/run and GitHub transport digests.
Both archives passed source, required-job/step, digest, embedded COMMIT/version and ELF-architecture validation.
Only the ARM64 archive was executed in the VM.
An earlier incomplete download was retained and rejected; a later bounded retry supplied the verified bytes.

## Scope and operator isolation

The native runs used software rendering.
Live mode used the repository's process-local zsh password-database fixture; the actual account remained bash.
Hardware rendering, default-bash Live support and native x86_64 acceptance are not established here.
The WebKit check establishes load state, not pixel rendering.

The unchanged harness launched the unchanged payload through an external recorded adapter.
The adapter gave each test frontend a private runtime directory and an absolute Wayland socket route;
the harness's compositor tools retained access to the existing desktop.
This prevented test writes to the real frontend's shared runtime configuration.
The helper identified test descendants and protected existing sessions by PID/start-time identity;
its failure path uses pidfds and the verified isolated zmx endpoint, never process-name kills.

[operator-provenance.json](operator-provenance.json) records actual executed helper hashes and source links.
[operator-validation.json](operator-validation.json) records 29 local synthetic helper checks, separately from native acceptance.
The earlier preparation helper had already staged and compiled the pinned assets successfully;
the corrected execution helper revalidated every asset and fixture hash before launching.
Root inspected the correction and repeated those local checks before native execution.
Reviews and execution used the authorized shared GitHub account; they do not constitute independent-account approval.

Raw logs, recovery journals and original captures remain local.
No screenshot was edited or published in this bundle.
No release publication or replacement of the installed app occurred during this acceptance run.
Verify the public records with `sha256sum -c SHA256SUMS`.
