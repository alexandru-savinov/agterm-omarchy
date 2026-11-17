# zmx patches

`scripts/setup.sh` applies every `*.patch` here, in name order, to a fresh checkout of `ZMX_REV` before
building, and every `ghostty/*.patch` to a private checkout of the ghostty revision that `ZMX_REV` pins
in its `build.zig.zon`. That second set is empty at the current pin, and the scripts accept an empty or
missing `ghostty/`. The digest of both sets is part of `.zmx-build-stamp`, so editing one rebuilds
zmx. A daemon already running keeps the zmx it was started by, and zmx v1 clients cannot talk to daemons
from before v1 (upstream froze a new IPC header), so a pin bump across v1 leaves earlier Live daemons
unreachable until they are reset.

Each patch is a diff `git apply` takes with `-p1`, against the tree the patches before it left: the zmx
patches on `ZMX_REV`, the `ghostty/` ones on the ghostty revision in that `ZMX_REV`'s `build.zig.zon`. To change one, check out both trees side by side as `zmx` and
`zmx-ghostty`, apply the ghostty patches to `zmx-ghostty` and then the zmx patches to `zmx`, since `0002`
makes zmx build against `../zmx-ghostty`. Edit, run `zig build test` in `zmx`, then regenerate the file
with `git diff --src-prefix=zmx-orig/ --dst-prefix=zmx/` from the tree the previous patch left.
`scripts/test-zmx-patches-linux.sh` runs upstream's own gates over the result: `zig fmt --check`,
`zig build test` and its bats suite. Moving `ZMX_REV` means re-applying them by hand where they no
longer apply, and re-running `check.py`.

- `0001-explicit-leadership.patch` lets a terminal own which client leads a session. A client attached
  with `ZMX_MANAGED=<token>` leads only by claiming at attach (`ZMX_MANAGED_CLAIM`), never by typing, and
  reports its role as a title under a reserved prefix that carries the token. `zmx screen` reads the
  daemon's own terminal, which always has the leader's layout, and `zmx type` queues input with an
  acknowledgement and without taking the lead. A session switch is ignored while the leader is managed.
  A client that does not set the variable behaves as upstream does. Its IPC tags are 200-203, clear of
  upstream's next free numbers, and a daemon without them answers `Unsupported`.
  `.claude/rules/control-api.md` owns how agterm uses it.

- `0002-private-ghostty-dependency.patch` points zmx's ghostty dependency at `../zmx-ghostty`, the
  private checkout `setup.sh` patches, in place of the zig package cache. Its context names the pinned
  revision, so a `ZMX_REV` that moves ghostty stops it applying. zmx's build reads the dependency hash
  as its reported ghostty version, so the patch writes that string in, marked `+agterm`.
- `0003-atomic-socket-publish.patch` gives a session socket its name only after it listens. Upstream binds
  the session path and then listens, and `zmx list`, attach and the commands that connect to a session
  delete a path that refuses a connection as stale. A probe between bind and listen can unlink the new socket
  as stale and leave the daemon unreachable. The patch binds and listens in `.s/<pid>` beside the sessions,
  a directory, which listing in every zmx version skips, then hard-links the socket to the session name. The link fails when the
  name exists, so a losing creator gets the same `AddressInUse` a bind gave it and never removes the
  winner's socket. When `.s` cannot be made or the staged path is too long for `sockaddr_un`, it binds the
  session name directly and keeps the old window.
- `0004-managed-exit-keeps-screen.patch` keeps the screen when a managed client exits. Upstream writes RIS
  on every attach exit, which erases a Live `--command --wait` pane's output under its hold prompt. A client
  with a valid `ZMX_MANAGED` instead turns off the input reports a program may have left on and resets SGR
  and the OSC colors. An unmanaged client and a session switch still write RIS.
- `0005-drain-output-before-exit.patch` keeps a session's last output when it ends. Upstream's attach client
  returned as soon as the daemon closed the socket or the socket hung up, dropping output it had read but
  not yet written to a busy terminal, and output still unread behind the hangup. On every disconnect path
  it now runs one loop that reads the socket and writes stdout, both nonblocking, bounded by an absolute
  two-second deadline and 1 MiB from the socket. The terminal reset an exiting client sends is a bounded
  write too, and the terminal settings are restored without TCSAFLUSH's wait for output to drain, so a
  client ended by SIGTERM on a terminal that stopped reading still exits. A Live command's final line under
  its hold prompt depended on that race.
- `0006-nonblocking-stdout.patch` keeps the attach client responsive to its daemon while the terminal is
  stalled (#409). Upstream writes session output to a blocking stdout, so a terminal that stopped reading
  held the client in `write` while the daemon was still connected, and it never saw the hangup. stdout is
  now nonblocking for the whole client loop, with its original flags restored on exit, including the exit
  upstream's SIGTERM/HUP/INT handlers start; the flags change only after those handlers are installed. Output is written
  only when poll reports room. A closed reader (POLLHUP/ERR/NVAL) ends the client with its write error
  instead of spinning. While 1 MiB is pending, the client stops reading further messages, so the
  backpressure reaches the daemon. That is a high-water mark, not a memory bound: zmx has no IPC message
  cap, so one whole message can still arrive past it (#409). Zig tests cover a stalled reader (the client
  returns within a generous 10 s of the hangup, nominally 2 s, so starved CI cannot flake it, and does
  not block forever) and a closed reader (it returns promptly); both run under a
  watchdog.
- `0007-incompatible-daemon.patch` names a daemon from another zmx release instead of attaching to it
  blind. v1 put a protocol byte in the message header, so a pre-v1 daemon's frames carry another value
  there, and its own parser reads a v1 `Init` as an unfinished frame and never answers. A header with
  another `protocol_version` marks the peer foreign: `zmx list` prints `err=IncompatibleProtocol` and keeps
  the socket, `attach` probes first and exits 1 with that reason, and a client loop that meets one ends.
  agterm's Linux launch resets those daemons (`.claude/rules/control-api.md`, Live sessions reset).
- `0008-daemon-drain-on-exit.patch` keeps a session's last output in the daemon when its shell exits
  (#447). Upstream's last pass wrote to each client once, and only to one whose socket polled writable,
  then shutdown freed every client's queued `write_buf`, so the final output for a client that was behind
  was dropped before `0005` could read it. On the shell's EOF the daemon now stops polling the PTY and keeps
  serving its clients until none has output queued, bounded by one two-second deadline shared by all of
  them. While it drains it takes a detach, a kill and Info and ignores every other message, so no new
  attach, input or command reaches a session that has ended; a kill and SIGTERM still end it at once, and
  a detach is unchanged. Each client connected at the exit is closed as soon as it has been sent
  everything, so a client that keeps reading sees no delay even while a stalled one holds the drain. The
  client's own `0005` drain starts at the daemon's hangup, so a terminal that never reads again now holds
  its client for up to about four seconds instead of two. Zig tests run a real daemon loop and client loop
  over a socket pair with 8 KiB buffers: the last output reaches a terminal that was full at the exit and
  then resumes; the daemon and then the client end on their deadlines when it never resumes; a draining
  session answers no late attach, input or command, while a kill ends it at once; and a reading client is
  let go while a stalled one still holds the daemon. Tests set the deadline themselves (long where they do
  not measure it), so a starved runner cannot miss it; the build outside tests always uses two seconds.
  This is the daemon half of the bounded drain asked for in umputun/agterm#998, on the Linux pin only; the
  macOS pin keeps its own patch set (#449).

`check.py <zmx>` drives managed clients on ptys of different sizes against one daemon in a throwaway
`ZMX_DIR`, prints one line per rule, and exits nonzero when any of them is broken. Linux CI runs it
against the vendored zmx, beside `scripts/tests/test_zmx_tty_restore.py`.
