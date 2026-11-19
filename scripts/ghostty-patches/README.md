# libghostty teardown patch

`setup.sh` applies these patches to `GHOSTTY_REV` before building GhosttyKit. The revision and patch
digest form `.ghostty-build-stamp`; CI keys its GhosttyKit cache on both. A changed patch rebuilds the
framework and resources. Never link a staged framework whose full stamp differs.

`0001` backports the mailbox drain from [ghostty PR 14444](https://github.com/ghostty-org/ghostty/pull/14444)
to `83fb956` (v1.4.0). During surface teardown the main thread joins workers and waits for in-flight Metal
frames. Both workers and frame completion callbacks can block posting to the app mailbox. A temporary
consumer frees queue capacity until `renderer.deinit()` finishes, and preserves other surfaces' messages
for the next app tick. The drain stops after a bounded final pass, filters messages from previous
closes as well as the queue, and releases owned payloads for discarded messages.

Remove the patch when `GHOSTTY_REV` includes an equivalent upstream fix whose drain also stays active
through `renderer.deinit()`. Upstream PR 14444's current ordering does not yet meet that condition.
When removing the final patch, remove `GHOSTTY_PATCH_DIR`, the digest calculation and apply loop in
`setup.sh`, and either set `GHOSTTY_STAMP="$GHOSTTY_REV"` or restore both the stamp comparison and stamp write to use
`GHOSTTY_REV` directly. Remove the patch digest from the CI cache key too. Preserve the three shutdown
regressions in `scripts/test-surface-teardown.py`: one title producer, eight title producers with one
closed, and eight idle surfaces with one closed. CI runs them against the Release app.

Apply the patch to a `GHOSTTY_REV` checkout before editing. Run `zig fmt --check src/App.zig src/Surface.zig`
and `zig build test -Dtest-filter=teardown -Demit-macos-app=false` there. Regenerate the patch with
`git diff -- src/App.zig src/Surface.zig` and verify `git apply --check` against a second pristine
checkout. Do not change queue capacity or move `ghostty_surface_free` off the app thread.

`0002` keeps a child's last output when its pty hangs up (#447). `ReadThread.gatherMainPosix` returned as
soon as its poll reported `POLLHUP` on the pty master, without reading. A Linux master reports `POLLHUP`
as soon as the last replica fd closes, with what the child wrote before closing still queued, so a child
that wrote and exited before the gather thread woke lost that output; a Live `--wait` pane lost its final
line under the exit hold. On a hangup the gather stage now reads and publishes what is queued until
`read()` returns 0, `EAGAIN`, `EIO` or another error, bounded by 4 MiB (`hangup_drain_max`), then exits.
On macOS the patch is harmless but recovers nothing: Darwin discards what is queued when the replica
closes (`revents` IN|HUP with `read()` returning 0), so the drain costs one `read()`; #449 tracks the
macOS side.
`gatherMainPosix` takes an outer poller. Production passes `std.posix`, and the test uses a poller
that writes and closes the pty replica before reading `POLLIN|POLLHUP` from the kernel.
The Linux-only tests also check a full pty queue, an empty queue, and the drain cap with `/dev/zero`.
The cap test has a five-second watchdog.
Remove `0002` once the pinned Ghostty drains queued pty output on hangup.

`0003` orders the exit message after the child's last output (#447). The process watcher can report the
exit while what the child wrote just before it is still queued in the pty or in the gather/parse ring, and
the surface writes `Process exited. Press any key to close the terminal.` into the terminal on the
`child_exited` notice, so under load the final line landed after the message (in 140 of 512 CPU-load
runs of the #447 trace with `0002` alone). The parse stage, now `ReadThread.parseMainPosix`, sets
`Exec.read_done` once the gather stage is done and the ring is empty; `processExitCommon` records the exit
and `ExitHold` sends the notice once `read_done` is set, re-checking on a 2 ms `xev.Timer` so the IO
thread never blocks. `threadMainPosix` also sets `read_done` when it returns early.
The Linux-only order and mailbox tests exercise the real gather and parse stages and the real
surface mailbox. The headless runtime in `src/apprt/none.zig` provides a no-op `wakeup` so the
mailbox test can enqueue notices without a GUI. The hold tests also check immediate release and the
bound, with a five-second watchdog on the bound test.
Do not use `FIONREAD` as a drained signal because it does not count the tty flip buffer.
Remove `0003` once the pinned Ghostty defers the exit notice until the reader has parsed the last output.

### Compatibility

On POSIX, if a background process keeps the pty open past `ExitHold.max_ms`, the exit notice waits
one second and libghostty logs a warning when it delivers the notice. Windows sends it immediately.

Linux CI runs the patch-added Zig tests, including the embedded-runtime tests, with
`scripts/test-ghostty-patches-linux.sh`.
New patch tests must be named declarations at file scope so the runner can verify each result.
