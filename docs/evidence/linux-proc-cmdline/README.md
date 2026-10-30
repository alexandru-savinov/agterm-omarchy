# Linux proc cmdline empty-argument proposal

Task [#101](https://github.com/alexandru-savinov/agterm-omarchy/issues/101), validated follow-up from review wave [#92](https://github.com/alexandru-savinov/agterm-omarchy/issues/92).
Implementation source inspected: `8458e16d06b2432cd6895955b6be972b1edae93d` (PR #6).
Author: `codex_proc_capture`, shared execution account; not independent approval.

This directory packages an inert patch and proposed tests for the Linux implementation owner.
It does not change the active app or its tests.

## Finding and reachability

`agterm-linux/Sources/AgtermLinux/LinuxProcessPolicy.swift:6` splits proc cmdline data with Swift's default `omittingEmptySubsequences: true`.
Linux encodes every argument with a terminating NUL, including empty arguments.
For example, `tool\0\0tail\0\0` represents `["tool", "", "tail", ""]`, but the parser returns `["tool", "tail"]`.
This changes argument count and positions, not merely display whitespace.

The live read is `GhosttySurface+RuntimeParity.swift:61–69`, which reads `/proc/<pid>/cmdline` and invokes this parser.
Its `foregroundCommand` method rejects idle shells but retains a non-shell program with empty arguments.
`LinuxForegroundCapture.swift:50` stores that argv as the session's foreground command.
`agtermCore/Sources/agtermCore/CommandRestore.swift` accepts nonempty argv[0] unless denied or carrying unreplayable scalars; it does not recover missing arguments.
`AppController+RestoreLaunch.swift:155–158` then uses `shellQuotedLine` for replay when restore is enabled.
The quoting function correctly quotes empty strings as `''`, but cannot restore arguments already discarded by capture.

Existing `LinuxPolicyTests.swift:44–45` checks empty data and ordinary nonempty arguments only.
The proposal preserves empty fields and removes exactly one final record terminator.
It also preserves an empty argv[0] so existing restore validation rejects that value instead of promoting the next argument to executable position.
Nonterminated input remains accepted for compatibility; this proposal does not redefine malformed-input policy.

## Artifacts and observed validation

- `proposal.patch`: narrow proposed change to `LinuxProcessPolicy.swift`.
- `LinuxProcCmdlineTests.swift`: proposed Swift tests for empty fields, restore quoting, terminators, and empty executable rejection.
- `verify_proc.py`: standard-library Python probe that launches only its own disposable child and reads its proc cmdline after a readiness handshake.

Executed on Linux with Python 3.12.12:

```sh
python3 docs/evidence/linux-proc-cmdline/verify_proc.py
```

Observed output:

```text
actual /proc suffix: b'\x00\x00tail\x00\x00'
empty argument indexes: [3, 5]
expected argc: 6 omitting-empty argc: 4
```

The probe verifies actual kernel bytes and compares preserving versus omitting empty fields in Python.
It does **not** execute or emulate a Swift application test; the Swift conclusion additionally relies on the inspected split call and downstream source trace.
The child exits after the probe closes its input; no external process or user session is inspected.
`git apply --check` passed against the inspected Linux source file.

## Owner replay and adoption

In a disposable checkout of `8458e16d06b2432cd6895955b6be972b1edae93d`, apply the patch using an absolute path to this directory's `proposal.patch`.
Copy `LinuxProcCmdlineTests.swift` into `agterm-linux/Tests/AgtermLinuxTests/` only in that disposable checkout, then run the Linux package's tests in its supported Swift/GTK toolchain.
Check that the empty-argument and empty-executable regressions fail before applying the patch and pass afterwards.
These Swift tests were **not compiled or executed here** because Swift is unavailable.
The active implementation owner decides adoption and normal test integration.

No full app launch, saved-session round trip, native VM, CI dispatch, deployment or release acceptance is claimed.
