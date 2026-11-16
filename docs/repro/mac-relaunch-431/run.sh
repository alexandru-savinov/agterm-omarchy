#!/bin/bash
# Reproduces #431 with a 70-line SwiftUI app and checks the proposed fix. macOS 14+, Xcode command line tools.
#
#   docs/repro/mac-relaunch-431/run.sh <empty work dir>
#
# It builds local.agterm.relaunch431-repro into the work dir and launches it the way a harness launches agterm
# (exec of the bundle binary). It talks to nothing but its own process. It finishes with a clean quit, which
# makes AppKit discard the saved state it created, so a second run starts from the same place.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work=${1:?usage: run.sh <empty work dir>}
mkdir -p "$work"
work=$(cd "$work" && pwd)
app="$work/Relaunch431Repro.app"
bin="$app/Contents/MacOS/Relaunch431Repro"
marker="$work/marker.log"
wait_s=10
# AppKit writes saved state about 15 s after launch; an unclean exit before that leaves nothing to restore
dwell=${REPRO_DWELL:-20}

mkdir -p "$app/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
swiftc -parse-as-library -O "$here/Repro.swift" -o "$bin"
codesign -s - -f "$app" >/dev/null 2>&1

pid=""
launch() { # $1: REPRO_FIX value
    : >"$marker"
    REPRO_MARKER="$marker" REPRO_FIX="$1" "$bin" >>"$work/app.log" 2>&1 &
    pid=$!
    disown "$pid"
}
seen() { grep -q "^$1 pid=$pid\$" "$marker"; }
wait_for() { # $1 milestone, $2 seconds; returns 0 when it appeared
    local i
    for ((i = 0; i < $2 * 4; i++)); do seen "$1" && return 0; sleep 0.25; done
    return 1
}
stop() { # $1: TERM (unclean, after the dwell) or USR1 (clean NSApp.terminate)
    [ "$1" = TERM ] && sleep "$dwell"
    kill -"$1" "$pid"
    while kill -0 "$pid" 2>/dev/null; do sleep 0.1; done
}
reopen() { open -g "$app"; } # what a Dock click sends: kAEReopenApplication
check() { # $1 label, $2 expected yes/no for window-task-ran
    wait_for did-finish-launching "$wait_s" || { echo "FAIL $1: the app never finished launching"; exit 1; }
    local got=no
    wait_for window-task-ran "$wait_s" && got=yes
    printf '%-58s window: %-3s (expected %s)\n' "$1" "$got" "$2"
    [ "$got" = "$2" ] || { echo "UNEXPECTED"; exit 1; }
}

# 0. start from no saved state, whatever an earlier run left: reopen if needed, then quit cleanly
launch 0
wait_for did-finish-launching "$wait_s"
wait_for window-task-ran 3 || { reopen; wait_for window-task-ran "$wait_s"; }
stop USR1

launch 0; check "1. launch after a clean quit" yes
stop TERM
launch 0; check "2. relaunch after SIGTERM (the #431 hang)" no
reopen
wait_for window-task-ran "$wait_s" && echo "   a reopen event (Dock click) brings the window back: yes" \
    || { echo "UNEXPECTED: reopen did not recover"; exit 1; }
stop TERM
launch 0; check "3. it stays stuck on every later launch" no
stop TERM
launch 1; check "4. relaunch with the fix (ApplePersistenceIgnoreState)" yes
stop TERM
launch 1; check "5. and again after another SIGTERM" yes

# leave nothing behind: a clean quit of a launch that does not ignore state discards the stale state
stop TERM
launch 0
wait_for did-finish-launching "$wait_s"
wait_for window-task-ran 3 || { reopen; wait_for window-task-ran "$wait_s"; }
stop USR1
launch 0; check "6. cleanup: a launch after the final clean quit" yes
stop USR1
echo "reproduced: SIGTERM leaves AppKit saved state; the relaunch restores zero windows and SwiftUI opens none"
