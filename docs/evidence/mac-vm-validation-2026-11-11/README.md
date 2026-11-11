# Mac and native Omarchy validation, 2026-11-11

Acceptance remains **incomplete**. This evidence supports [goal #305](https://github.com/alexandru-savinov/agterm-omarchy/issues/305); it does not promote current `main` or the frozen candidate to a release.

## Results and identities

| Lane | Exact subject | Demonstrated result |
| --- | --- | --- |
| Native ARM ordinary | `bec375d70025ce3ec0e93fb9a0f3bb3be5ecae9d`, trusted run [41532553668](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/41532553668), attempt 1 | 7/7 checks and independent cleanup pass in both new attempts. |
| Native ARM Live | Same immutable artifact | 9/9 checks pass twice, including the same shell PID across frontend restart and owned daemon cleanup. |
| Native actual-bash Live eligibility | Same immutable artifact, unchanged harness, Live preload removed | Primary PTY check passes; requested Live reports active `none` because the passwd login shell is not zsh. Live harness fails with clean teardown, demonstrating the documented eligibility restriction. |
| Native config, attempt 1 | Same app; original probe `c521ff81…` from the same source | Fails before assertions: probe waited for an unsupported control-socket override. Forced recovery and a generated `dconf` entry remain recorded. |
| Native config, attempt 2 | Same app; separately reviewed probe from [PR #312](https://github.com/alexandru-savinov/agterm-omarchy/pull/312), `e77b6ad448677e00cafd14a36c557b96affb6678` | 3/4 checks pass. The final noisy-title shutdown exceeds 10 seconds, needs another signal, and retains its generated theme directory. Overall failed. |
| Installed Mac ordinary comparison | agterm 0.38.1 / `09727610` | 14/14 checks pass; original app/session identities and installed bytes preserved; no test process remains. |
| Installed Mac Live comparison | Same installed bundle | 11/11 functional checks pass, including shell persistence and hidden-pane restart. Empty daemon inventory, but the session host needs explicit cleanup: overall failed under the strict no-recovery criterion. |
| Original Mac core baseline | `25edfe31a34a9cce9c4cbd68a106222e4d73aea8` | 4,117 passed / 1 failed / 9 skipped; zsh startup bypassed the fake terminfo compiler. |
| Reviewed Mac core | [PR #311](https://github.com/alexandru-savinov/agterm-omarchy/pull/311), `ab94f48fc94e9a4e2db82f2feb5b8f38d462f341` | 4,129 passed / 0 failed / 9 skipped, out of 4,138 definitions / 182 suites. |
| Same reviewed core, available zmx staged privately | Same source, installed zmx hash below | Full suite: **4,137 passed / 0 failed / 1 skipped (fish)**. The eight host definitions previously skipped also pass in a separate 26-test group. |
| Integrated Mac core, available zmx staged privately | `2ddd93af8cd0b09a7c3fe668e6cf1f759949393b` | **4,148 passed / 1 failed / 1 skipped (fish)** out of 4,150 definitions / 182 suites. The known terminfo fixture failure remains; PR #311 is not in this head. All 26 host tests pass. |

The core revisions have different test inventories. The terminfo test-file blob is identical at the original baseline and PR #311's base; the changed total is not attributed solely to that fixture fix. All full runs were unfiltered. The reviewed-PR run with staged zmx used `--skip-build` with the already built, unchanged reviewed source and a newly satisfied runtime prerequisite. The later integrated-main run performed its own fresh build.

Native application SHA256: `c6f8ed5d6cd16be4af4997d363814cc2da3ab58f9e18d9ef81d17775d9a91946`.
ARM archive SHA256: `d553f29e9fd978e83d60df73b1ef668292bc8414ec5b6617c94c0dc08d0b6995`.
Native bundled zmx SHA256: `7d4d530c2741e7c8edc1ddbfadf528a247d19f032e660aeb7eaf0b1f6288d310`.
Unchanged native harness SHA256: `9fe046d6fbbf0dbc79e0591b2fbe8869af4f79604d6a2f7a470c5750044cee40`.
Installed Mac app SHA256: `04abc755511fb07456070cc8b12348c2674dd1ad0530f78d9a36837b43e72306`.
Installed Mac zmx SHA256: `4db635396d83ded73e102fb365375a9216c01da515d58c796537b83210b6eb01`.

## Native boundary

Fresh private guest stages, fresh process baselines and actual running-image hashes bind both attempts. The archive was rehashed locally and in the guest. The second stage copied the verified guest archive using exclusive destination checks and verified its complete digest again. Preparation and execution helpers changed only their bound probe digest; their 29 preparation/process and 10 outer-probe safety checks passed again. [The binding](native-attempt-2-binding.json) records parent and selected template hashes, the probe source, and the same-account source review.

Both modes use software rendering and a private runtime adapter. Live additionally uses a process-local zsh login-shell fixture; ordinary mode does not preload that fixture. The real Omarchy login shell remains bash. The unchanged harness records red-to-green rendering in the focused, test-owned window's cropped area. This is narrower than general visual acceptance: it does not establish hardware acceleration, theme/appearance fidelity, default-bash Live behavior, x86 native operation or the evolving main frontend. No desktop image is included here.

The revised probe demonstrates two exact-image, mapped native Wayland instances with real PTY execution; disjoint config directories; idle-instance cleanup while the peer remains usable; and a bounded pair of theme files after 32 reloads and 32 background/reset sequences. A bounded eight-second title producer was alive and had emitted at least 100 titles when the final shutdown was requested. That frontend exceeded the unchanged ten-second deadline. Recovery removed the remaining owned process; the retained generated directory correctly keeps the result failed. Source diagnosis and the next candidate test are tracked in [#199](https://github.com/alexandru-savinov/agterm-omarchy/issues/199#issuecomment-6805736269).

Installed/extracted payloads, actual login shell and snapshotted shared runtime remained unchanged. The first attempt had no preexisting protected app processes; the second observed and preserved two. These are fresh observations, not inherited historical session counts.

Two later configuration-only diagnostic replays retain the unchanged probe and payload. Replay 1 passed all four checks; its right shutdown took 3.218 seconds and no debugger attached. Replay 2 reproduced the timeout and captured the verified test-owned frontend after its control socket was unlinked. GDB attached and detached successfully; main, io, io-reader and io-gather were waiting on futexes, with library frames inside the exact shipped libghostty. The renderer thread had exited. The library is stripped and internal unwinding is incomplete, so the source-supported mailbox/join cycle remains a hypothesis. These diagnostic outcomes demonstrate intermittency and do not supersede failed acceptance. The source fix is tracked in [#320](https://github.com/alexandru-savinov/agterm-omarchy/issues/320).

A separate symbol-bearing diagnostic variant then reproduced the timeout and resolved the waiting calls. Its private payload copy changes only `lib/libghostty.so`, rebuilt natively from the same pinned Ghostty and frozen patches with `-Dstrip=false`; the app image and probe stay unchanged. Main waits in `Surface.deinit` joining the I/O thread; I/O waits in `Exec.threadExit` joining the reader; the reader waits in the full 64-entry app mailbox while delivering an OSC title. GDB verified the diagnostic library mapping and detached successfully. This directly demonstrates the cycle in that variant, with three probe checks passing and final shutdown failing. One recorded recovery signal removed the last owned process; generated config remains as failure evidence. Debugger timing and changed unwind settings prevent treating this as frozen-artifact acceptance or as symbolization of the old library. Both private payloads, protected processes and the real login shell were preserved.

The additional actual-shell lane removes only the outer operator's Live preload assignment. The frozen app executes terminal input, then reports `configured: live`, `active: none` and the documented non-zsh eligibility reason. Both cleanup audits pass without recovery. This failed Live harness verifies the fallback behavior on the VM's unchanged bash account; it does not establish bash Live support. An earlier preparation attempt failed before app launch because a private helper shadowed Python's `operator` module; that failure is retained separately.

## Mac boundary

Host: macOS 26.7.1 arm64, Apple Swift 6.4, selected Command Line Tools. Full Xcode is absent. There is no fork Debug/Release AppKit build or PR #296 GUI acceptance in this evidence.

Runtime comparisons use new LaunchServices instances with private state, explicit control sockets and test-owned PTYs. The original app process and seven observed sessions in its current window were preserved. No installed app or user settings were replaced. Ordinary coverage includes primary/split/scratch PTYs, separate output, hide/show retention, pane-owned status, a local HTML overlay's loaded state, continued PTY input and closure. Loaded state is not a screenshot assertion.

Live coverage includes a long creation command executed once, real primary/split zmx backing, hidden-pane restart, unknown-pane refusal, frontend quit/relaunch, the same surviving shell, and no command replay. A separate minimal reproduction identifies the remaining process after empty inventory and frontend exit as the newly created session host. Root checked its PID/start/image identity before signaling it. No test-owned process remains. This records the teardown limitation without recasting successful persistence as failure.

A later [source lifecycle review](https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6806845343) found that the bundle's declared upstream revision intentionally keeps the session host alive while idle, with no idle-exit deadline. The five-second automatic-exit expectation belongs to these private probes, not the product contract. Neither probe sent the host's administrative `stop` request. Their strict cleanup results remain failed; the persistent host is consistent with the declared design. Binary-to-source attribution uses the bundle's declared commit and unchanged helper hash, without a reproduced build or release-DMG comparison.

The first ordinary direct launches did not create the control socket; LaunchServices supplied the working launch path. Two early Live attempts refused the expected endpoint because Python and Foundation spell equivalent Darwin temporary paths differently. Those private failed attempts remain retained; they do not count as passing runs. The successful functional Live run still fails its cleanup criterion.

Core tests used the installed CLT framework and macro plugin through command-local flags, with the native build engine. No global toolchain change or weakened assertion was made. See the recorded argv in [the reviewed manifest](mac-core-reviewed.json). Staging copied only the existing bundled zmx into the private checkout's ignored resource path. The fixture helper hashes and actual server tests are recorded separately; this is not a source-built zmx claim. Fish is the one remaining explicit prerequisite skip.

## Published evidence

- [Native attempt 1](native-attempt-1.json), [attempt 2](native-attempt-2.json), and [revised binding](native-attempt-2-binding.json).
- Configuration-only [diagnostic replay 1](native-diagnostic-1.json), [replay 2](native-diagnostic-2.json), and [selected backtrace blocks](native-diagnostic-2-threads.txt), with debugger and symbolization limits.
- The separate [symbol-bearing diagnostic build and replay](native-symbol-diagnostic-1.json) and [selected symbolized stacks](native-symbol-diagnostic-1-threads.txt), including exact compiler/library/patch identities and the earlier supervisor startup failure.
- [Actual-bash Live eligibility](native-default-shell-live.json), with the unchanged harness, explicit preload-removal binding, original setup failure and actual restore response.
- [Mac ordinary](mac-ordinary.json), [Live](mac-live.json), and [minimal cleanup reproduction](mac-live-cleanup.json).
- [Core baseline](mac-core-baseline.json), [reviewed full run](mac-core-reviewed.json), [full run with staged zmx](mac-core-staged.json), and [real host fixtures](mac-host-fixtures.json).
- [Fresh integrated-main core run](mac-core-integrated-main.json), with its exact failing source, one retained skip, all 26 host test names, binary hashes and private original-log binding.
- The three `*-tests.log` files contain explicitly labeled test-result extracts. Each carries the original complete log's SHA256; it is not presented as the complete raw log. Literal split completion lines remain intact. [Interleaving support](interleaved-results.json) records all three affected raw fragments and explicitly labeled reconstructions, so an inserted SwiftPM warning does not silently remove a completion or alter the verified totals.
- [SHA256SUMS](SHA256SUMS) covers the published files. JSON reports select public, relevant fields and retain their private originals' hashes. VM identifiers, private environment values, full desktop images and absolute test-stage paths are excluded.

The public [goal](https://github.com/alexandru-savinov/agterm-omarchy/issues/305), [native task](https://github.com/alexandru-savinov/agterm-omarchy/issues/199), and [Mac fixture task](https://github.com/alexandru-savinov/agterm-omarchy/issues/310) remain authoritative for later changes. Reviews here use a shared account and do not constitute independent account approval. Hosted Linux/lint checks, subsequent source integration, and exact-current-candidate native acceptance remain separate gates.
