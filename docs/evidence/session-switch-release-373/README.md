# Session-switch release recheck evidence (#373 / PR #374)

Agent: codex_theme_failure

Source: dee964208603f55e0f04f6c408a8002182b2bfd1, based on 2c486ebe6dfb22605fcdd5fde19063d435d24b03.
This is local Ubuntu 24.04 x86_64 validation; hosted checks and native Omarchy acceptance are separate.
The original baseline is the observed unmodified decision in hosted run 43142387596, documented at issue #357 comment 6813377397.
That run captured a real held=true query followed by held=false without another Control event and failed the search-entry commit assertion.
This does not prove physical keyboard state at the first sample or explain reconnect/older failures.

## Effective production sensitivity checks

- `no-recheck.patch`: disable scheduling for an initial true sample. Actual opt-in GTK run fails 9 issues because the required retry is absent. No fixture assertion is modified.
- `no-generation.patch`: remove the three generation comparisons. Actual GTK run fails 18 issues, including stale reads, incorrect newer-cycle selection and clearing the newer timer handle.
- Both patches apply to the final source's unchanged picker file. Neither mutation was committed or pushed to the production branch.
- `agterm-373-restored.log`: restored picker bytes, actual GTK tests pass in 2.757s. At that point runtime HEAD was 58028fc7 plus test bytes exactly matching 194af553; it is not represented as a clean committed-head run.
- `agterm-373-final-gtk-ui.log`: clean dee96420, same actual GTK tests pass in 2.539s; all seven original physical session-switch scenarios pass, including genuine two-Control and preheld-Control controls. Original assertions and waits are unchanged.

The callback fixture has five test functions / twelve argument cases.
It queues an actual GLib idle barrier, captures the actual MainTimer callbacks, and deliberately delivers callbacks even after cancellation.
The test suite uses a real registered AppController and real GTK widgets; sampled device values are supplied through the internal reader argument to deterministically replay the observed state sequence.
The initial fixture attempt is retained separately in `fixture-development/agterm-373-positive-initial.log`.
Initial fixture-development errors (an undrained 100ms startup idle and an unparented test popover) were corrected before the effective negative/restored sequence; those errors are not counted as production regressions.

## Other checks

`agterm-373-full-linux-lint.log`: strict SwiftLint with `--no-cache` (read-only runtime cache path), then 727 Linux tests in 105 suites pass in 45.752s.
The opt-in suite is skipped in this ordinary test command; its separate positive runs above are the acceptance evidence.
`agterm-373-core.log`: all 4109 core tests in 179 suites pass in 47.711s.
Actionlint, diff whitespace checks and all 16 focused CI tests pass.
A dedicated isolated GTK invocation was added to the existing hosted architecture matrix.

`source-and-binaries.json` records clean final runtime source identity and file/binary hashes.
`SOURCE_STAMP` records the reused five-patch vendor build; input diffs and both vendor validators passed before reuse.
The final app and control client were rebuilt from the tested source.
All sources, fixtures and runtime inputs were restored before the final checks.

No diagnostic logging or forced state injection is shipped.
The one 50ms recheck is bounded: true/nil at that recheck leaves the cycle for a real later release; it does not guarantee recovery from arbitrary longer backend lag.
