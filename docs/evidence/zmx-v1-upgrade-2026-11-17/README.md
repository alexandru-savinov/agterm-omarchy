# Live sessions across the zmx v1 upgrade — native Omarchy, 2026-11-17

Question (PR #441 review): what does a user with Live sessions see after upgrading from prerelease
`omarchy-dev-b1356f0ad7ae-run43761095738` (zmx v0.8.1) to a build pinned to zmx v1?

Each run used the Omarchy VM (Arch Linux ARM, aarch64, Hyprland, software GL). Everything lived in a private
prefix, HOME, XDG directories and agterm state, plus the private zmx socket directory derived from that state,
removed afterwards. The VM's own agterm install, state and daemons were not touched.
[`upgrade_probe.py`](upgrade_probe.py) is the exact script: it unpacks the prerelease, starts a Live zsh
session (`command = /usr/bin/zsh`, since the VM user's login shell is bash), quits with SIGTERM, replaces
the prefix with the new payload, relaunches on the same state, then reads the pane back, types into it
and lists the daemons.

| Payload | sha256 |
| --- | --- |
| prerelease aarch64 | `38b004cdf02234a1fc804ab8a06cbd06e1a15ee0773d77b884154126b2488b41` |
| PR #441 at `1cefbfb5` (zmx v1, no fix) | `44d974b5c70898ba485bde681bab7b5495093e13691570763d4794b2023a27e6` |
| PR #441 at `668156a3` (with the fix) | `7fcad5da9c87717e075a08774d9e468a78c5d56ba17075ccc97af5b68941acc9` |
| PR #441 at `b812a6ec` (fix, shortened toast) | `0c2921b8ca1a85e090252f0e3e15b22ebda8701ca2bf42d8877063d2d2b6c781` |

## Before the fix: a blank pane that takes no input

The app started and showed the session as realized and zmx-backed, but `zmx list` (in agterm's inventory)
observed the daemon as `unreadable`, and bundled zmx v1 listed it as `err=Unexpected status=unreachable`.
`session text` was empty, typed input never ran (no marker within 8 s), and a v1 `zmx attach` client stayed
attached to the old daemon with nothing to show ([before-fix-dead-pane.png](before-fix-dead-pane.png)).
The cause is the IPC header: v1 cannot frame anything the old daemon sends, and the old daemon reads v1's
`Init` as a frame it never completes.

## After the fix: the session is reset and works

Same result on both fixed payloads; the last run (`b812a6ec`) is the one pictured.

- zmx v1 lists the old daemon as `err=IncompatibleProtocol` (zmx patch 0007).
- Before any pane spawned, the app found the old daemon (pid with no controlling terminal, `tty_nr=0`), ended it
  with SIGTERM, and logged `Live reset at launch: 1 pane(s) on an incompatible zmx; ended 1 daemon(s), 0 still running`.
  The old daemon's own log shows a clean shutdown and its shell got SIGHUP.
- The pane restored on a new daemon: typing executed (new shell pid) and read back.
- `tree` and `zmx list` read back `liveReset.last` = panes confirmed 1, killed 1; sessions affected 1, reset 1.
- The first window showed `Restarted 1 live session left by an older agterm` as a toast
  ([after-fix-reset-toast.png](after-fix-reset-toast.png)). The first wording, on `668156a3`, was cut off on
  this 910 px window; this one fits.
- The window mapped about 1.25 s after spawn, and the control socket answered by about 3.1 s.

What is lost: whatever ran inside the old Live session. Its daemon could not be reattached by any v1 client,
so ending it is the only way to give the pane back; in Re-run commands mode the captured command is pre-filled
at the new prompt as after any lost daemon.

The two "Process crashed" banners in the screenshots are the VM desktop's own notifications, unrelated to agterm.
