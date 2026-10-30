# Linux URI-list path escaping proposal

Source reviewed: `8458e16d06b2432cd6895955b6be972b1edae93d`.
Task: https://github.com/alexandru-savinov/agterm-omarchy/issues/103
Review wave: https://github.com/alexandru-savinov/agterm-omarchy/issues/92

This directory contains an inert patch for owner review; no active application or test source changes are included in this PR.
The patch SHA256 is `23258ae578e2d8ae81d38ee742c9dc289296acd18dd2f1d3026f40570a742a64`.

## Finding and intended behavior

`LinuxPastePolicy.swift` decodes each file URI with `URL.path`, then joins the paths without escaping.
`ShellEscape.dropPayload` sends that result to the string-drop callback (`GhosttySurface.swift:961`), which injects the text.
A URI containing `one%20two` therefore becomes two shell words instead of one filename.
The sibling native `GdkFileList` callback already calls `ShellEscape.path` per path (`GhosttySurface.swift:982`).
The clipboard path (`GhosttyApp.swift:408`) shares the Linux URI decoder, so the proposal intentionally aligns both URI-list drop and clipboard file paste.
Plain text and mixed URI/plain-text payloads keep existing behavior.

macOS intent was checked before changing the shared Linux helper:
`agterm/Ghostty/GhosttyCallbacks.swift:206-221` documents shell-escaped file paths for clipboard and drop, joins the escaped paths with spaces, and preserves plain text verbatim.
Its `urlText` calls `ShellEscape.path`.
The proposed change is Linux-only and does not change `agtermCore/ShellEscape.swift` or macOS source.

## Proposal and verification

Apply `uri-paths.patch` to the pinned source for review.
It maps each decoded path through the existing `ShellEscape.path` before joining, updates the old unescaped-space expectation, and adds literal expected-output regressions for two files, percent-decoded spaces/parentheses, comments/CRLF, a plain path, plain text, and mixed input.
The expectations are explicit strings, not computed using the helper under test.

Observed: `git apply --check` against the exact pinned source exits 0.
Not run: Swift compilation or tests; no Swift compiler is on PATH in this environment.
Proposed owner validation: `cd agterm-linux && swift test --filter LinuxPolicyTests`, then file URI-list drop and clipboard acceptance with a filename containing spaces, comparing native file-list drop.
No GUI/VM/application or command-injection demonstration was run.

This patch follows the existing quoting helper's contract; it does not independently prove every shell/control-character edge case safe.
Same-account agent proposal, not independent approval.
