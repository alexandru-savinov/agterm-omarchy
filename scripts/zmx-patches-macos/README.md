# zmx patches for the macOS app

`scripts/setup.sh` builds the macOS app's zmx from these, on zmx v0.8.1 with zig 0.16. They are
`scripts/zmx-patches` as of `ade88d61`, before Linux moved to zmx v1. The macOS app stays here until its
launch path can reset the daemons a v1 client cannot reach; `scripts/setup.sh` says why.
`scripts/zmx-patches/README.md` describes 0001-0006, which do the same on both pins.

- `0007-daemon-drain-on-exit.patch` delivers output still queued for clients when the shell exits,
  under one shared two-second daemon deadline.
  A draining daemon accepts only detach, kill and inventory requests, and keeps a drained leader
  connected until teardown unlinks the session socket and closes all clients without role changes.
  Request dispatch checks the current drain state, including the poll iteration that first sees EOF.
  The patch ports the daemon logic and deterministic busy-client, stalled-terminal, cancellation and
  managed-follower tests from the Linux stack onto this pin's APIs; the existing client implementation
  in 0005-0006 stays unchanged.
  Native `zig build test` exercises the real daemon and client loops through private PTYs and sockets.
  A test-only gate makes EOF and a queued Run ready in one poll; reverting just the live-state guard
  must fail its no-acknowledgement assertion.
  Failure-injection cases also check staged fixture startup cleanup and preserve an unrelated pipe.
  These tests cover the queued-client drain; they do not establish that every final-output loss path
  investigated in #447 is fixed.

- `0008-detached-create.patch` and `0009-single-da-answer.patch` are upstream agterm's `0005` and `0006`
  (v0.43.0), ported onto this stack: the detached create agterm-server starts panes with
  (`attach --detached`, `ZMX_EXIT_FILE`), and the daemon dropping a DA1/DA2 query it already answered before
  a client's Init. On the shell's EOF `0008` sets `child_exited` beside `0007`'s drain deadline instead of
  stopping the daemon at once. `scripts/setup-zmx-linux.sh` builds this set for agterm-server's musl targets.

- `ghostty/0001-formatter-unstyled-blanks.patch` fixes the snapshot a re-attach replays. The formatter
  wrote cells a program skipped with a cursor move as spaces under the previous cell's style, so a gap
  after a colored run came back painted; a skipped cell now closes the style first. Upstream fixed it in
  ghostty-org/ghostty#14725, which zmx v1's Ghostty pin carries.

`scripts/test-zmx-patches-linux.sh` checks that this set still applies to the pin `scripts/setup.sh` names.
