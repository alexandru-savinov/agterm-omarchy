# Exact combined candidate: native Omarchy acceptance

Root ran the unchanged ARM64 archive from [trusted run 41357189891](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/41357189891),
source `6775c33626925adc8a17e9ed7700f296f38d7688`, on the existing Omarchy ARM64 VM.
Execution ran from 2026-11-01T23:11:42.305326+00:00 to 2026-11-01T23:11:51.133538+00:00.
The [root execution report](https://github.com/alexandru-savinov/agterm-omarchy/issues/185#issuecomment-6565317571)
and this bundle retain the exact candidate identity after the workflow-security and notice-retention integration.
The earlier fd85fc58 evidence remains unchanged and bound to its own archive.

| Gate | Result |
| --- | --- |
| Ordinary native Wayland | 7 checks passed, exit 0 |
| Native Live mode | 9 checks passed, exit 0; bundled zmx executed successfully |
| Frontend restart | Shell PID 657142 persisted across restart and was absent after cleanup |
| Harness cleanup | Passed, including sentinel creation, observation before and after daemon teardown, then confirmed kill and absence |
| Separate process audit | 16 ordinary and 31 Live process identities tracked; no remaining test processes, new app processes or retained test state |
| Preservation | All 3 protected pre-existing process identities survived; installed payload, actual login shell and shared Ghostty runtime files remained unchanged |
| Payload identity | One ordinary and two Live app image hashes match the candidate; extracted file bytes, modes and symlink targets remained unchanged |

[native-report.json](native-report.json) retains the mode results, execution identities and cleanup evidence.
There was no forced cleanup, timeout, received signal or operator stderr.
The preserved-process count describes process identities, not a session count.

## Exact archive and build evidence

- ARM64 archive SHA256: `2b629e40475b66a99c3c2599eaae113bb1f7ae7d4a739f95f243563df70d2890`.
- Bundled zmx SHA256: `ed4d6c45f305147c5c4b19661df36e90e75dad2f061d3d595d14bd0257f45f6a`.
- Running app SHA256: `efafbce6ac0956bc8c32f7036d92b6fb4afb45ecb25382b99bf867f31d0a4b37`.
- Unchanged harness SHA256: `9fe046d6fbbf0dbc79e0591b2fbe8869af4f79604d6a2f7a470c5750044cee40`.

[artifact-provenance.json](artifact-provenance.json) is the unchanged validator's promotion manifest.
[runtime-identities.json](runtime-identities.json) binds both original downloaded archives to their embedded source/version and executable hashes.
Both architectures passed trusted source/workflow/current-branch, required-job/step, transport digest, safe-tar and ELF architecture checks.
Only the ARM64 archive was executed in the VM.
The first observer exhausted its polling budget while CI remained active; the follow-up observed success, and both downloads completed within their bounds.

[notice-validation.json](notice-validation.json) records each archive's 15 retained notice files and complete 16-library hash inventory.
Pinned texts match this source byte-for-byte.
The notice profile retains explicit coverage limits; this result does not certify all transitive attribution or release clearance.
Artifact records retain their original pre-native scope; the subsequent execution is recorded in the native report.

## Runtime conditions and operator evidence

The native runs used software rendering.
Live mode used the repository's process-local zsh password-database fixture; the actual account remained bash.
Hardware rendering, default-bash Live support and native x86_64 acceptance remain unverified.
The WebKit check establishes overlay load state; pixel output remains unverified.
The earlier hosted reconnect failure remains unclassified; its unchanged assertion passed in this candidate's matrix.

The unchanged harness launched the unchanged payload through the recorded external adapter.
Each test frontend received a private runtime directory and an absolute Wayland socket route;
the harness retained access to the existing compositor for focus, input and screenshots.
The generated-runtime collision in #124/#126 remains an application limitation; this operator isolated the test without changing application code.
Test descendants and protected processes were identified by PID/start-time identity.
The failure path uses pidfds and the verified isolated zmx endpoint, without process-name kills.

[operator-provenance.json](operator-provenance.json) records the actual executed preparation and run helper hashes.
Both used the reviewed corrected templates with only the source/run/stage identity adapted.
[operator-validation.json](operator-validation.json) records 29 local synthetic checks, repeated by root before execution.
Reviews and execution used the authorized shared GitHub account and do not constitute independent-account approval.

Raw logs, recovery journals and original captures remain local.
No screenshot was edited or published in this bundle.
No installed-app replacement or release publication occurred.
Verify the public records with `sha256sum -c SHA256SUMS`.
