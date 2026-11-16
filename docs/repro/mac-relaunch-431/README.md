# #431: no window and no control socket after an unclean exit

`run.sh <empty work dir>` builds a 70-line SwiftUI app with agterm's three launch ingredients and shows that a
relaunch after SIGTERM opens no window, that a reopen event (a Dock click) recovers it, and that registering
`ApplePersistenceIgnoreState` fixes it. It takes about two minutes, because AppKit writes its saved state only
about 15 seconds into a run. It ends with a clean quit, which discards the saved state it created.

`REPRO_KEEP_RESTORABLE=1` leaves windows restorable; the relaunch then restores the window and the hang is gone,
so the `isRestorable = false` opt-out is one of the ingredients.
