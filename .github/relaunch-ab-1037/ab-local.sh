#!/usr/bin/env bash
# Local A/B for umputun/agterm Discussion #1037 with the two Debug arms the arms workflow built and uploaded,
# plus the cold agterm://session link and cold folder smoke checks on the patched arm.
#
#   ab-local.sh <artifact dir: agterm-master.zip, agterm-patched.zip, hashes.txt> <output dir> <winctl binary>
#
# Isolation: every launch gets a fresh short AGTERM_STATE_DIR and AGTERM_CONTROL_SOCKET under /tmp/ab37, with
# AGTERM_*, GHOSTTY_*, CLAUDE*, CODEX*, ZMX* and __CFBundleIdentifier scrubbed and the live app's PATH entries
# dropped. Only processes under the staged bundle path are ever signalled. The Debug bundle id
# (com.umputun.agterm.debug) is its own preferences and saved-state domain; both are backed up first and restored
# exactly at the end, as is Launch Services registration.
#
# Both arms launch from ONE bundle path, swapped in between; AppKit keys saved state on the signing identifier, so
# both meet the same state. The stale state is made once by the master arm: a launch that shows its window, ~25 s
# for AppKit to write, then SIGKILL. Order is master -> patched -> master, nothing quits cleanly in between, and no
# launch argument or `defaults write` is used. A window is counted from the window server by pid (winctl), never
# through System Events or a second `open`, which would deliver the reopen that recovers a stuck launch.
set -uo pipefail

ARTIFACTS=$1
OUT=$2
WINCTL=$3
TRIALS=${TRIALS:-3}
WAIT=${WAIT:-30}
BID=com.umputun.agterm.debug
ROOT=/private/tmp/ab37 # what an open-launched process reports, so the pgrep patterns match
FIXED=$ROOT/app/agterm.app
OLD=$ROOT/old
BACKUP=$ROOT/backup
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
mkdir -p "$ROOT/app" "$ROOT/arms" "$OLD" "$BACKUP" "$OUT/trials"
RESULTS=$OUT/results.tsv
SMOKE=$OUT/smoke.tsv
printf 'arm\tmode\ttrial\talive\twindow_at_s\tsocket_at_s\tverdict\twindows_onscreen_all\n' > "$RESULTS"
printf 'check\tresult\tdetail\n' > "$SMOKE"
staged=0
sdn=0

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$OUT/ab.log"; }

SCRUB=(env)
while IFS= read -r v; do SCRUB+=(-u "$v"); done < <(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' |
    grep -E '^(AGTERM_|GHOSTTY_|CLAUDE|CODEX|ZMX|__CFBundleIdentifier$|TERM_PROGRAM)')
CLEAN_PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -v 'agterm.*\.app/' | paste -sd: -)
SCRUB+=(PATH="$CLEAN_PATH")

ctl() { perl -e 'alarm shift; exec @ARGV' 10 "$FIXED/Contents/MacOS/agtermctl" "$@"; }
app_pids() { pgrep -f "$FIXED/Contents/MacOS/agterm( |\$)"; }

saved_state_dir() {
    local c m
    for c in "$HOME/Library/Daemon Containers"/*/Data/Library/"Saved Application State"; do
        m="$c/ApplicationMapping.plist"
        [ -f "$m" ] || continue
        python3 -I -c '
import plistlib, sys
d = plistlib.load(open(sys.argv[1], "rb"))
for key, value in zip(d, d[1:]):
    if isinstance(key, str) and isinstance(value, dict) and value.get("protected", {}).get("signingIdentifier") == sys.argv[2]:
        print(key)
' "$m" "$BID" | while IFS= read -r u; do echo "$c/$u.savedState"; done
    done | head -1
}

kill_all() {
    pkill -KILL -f "$FIXED/Contents/" 2>/dev/null
    for _ in $(seq 1 50); do
        pgrep -f "$FIXED/Contents/" > /dev/null || return 0
        sleep 0.2
    done
    log "WARN processes from $FIXED still alive: $(pgrep -fl "$FIXED/Contents/" | tr '\n' ';')"
}

stage() {
    kill_all
    staged=$((staged + 1))
    if [ -e "$FIXED" ]; then
        "$LSREGISTER" -u "$FIXED" 2> /dev/null
        mv "$FIXED" "$OLD/$staged-agterm.app"
    fi
    ditto "$ROOT/arms/$1/agterm.app" "$FIXED"
    "$LSREGISTER" -f "$FIXED"
    ARM=$1
    log "staged $1: dylib $(shasum -a 256 "$FIXED/Contents/MacOS/agterm.debug.dylib" | cut -c1-16), $(grep -a -c ApplePersistenceIgnoreState "$FIXED/Contents/MacOS/agterm.debug.dylib") ApplePersistenceIgnoreState match(es)"
}

new_sd() {
    sdn=$((sdn + 1))
    SD=$ROOT/s$sdn
    SOCK=$ROOT/k$sdn.sock
    mkdir -p "$SD/windows" # skips the first-run welcome alert
}

saved_state() {
    local f n=0
    {
        echo "== $1 ($(date -u +%H:%M:%S))"
        for f in "$SS"/*; do
            [ -f "$f" ] || continue
            n=$((n + 1))
            printf '%s  %6s  %s  %s\n' "$(stat -f '%Sm' -t '%H:%M:%S' "$f")" "$(stat -f %z "$f")" "$(shasum -a 256 "$f" | cut -c1-16)" "${f##*/}"
        done
        echo "files: $n"
    } >> "$OUT/savedstate.log"
    STATE_FILES=$n
}

launch() {
    local mode=$1
    LAUNCHED_AT=$(date '+%Y-%m-%d %H:%M:%S')
    PID=
    case $mode in
        open-g) "${SCRUB[@]}" open -n -g --env AGTERM_STATE_DIR="$SD" --env AGTERM_CONTROL_SOCKET="$SOCK" "$FIXED" ;;
        exec)
            "${SCRUB[@]}" AGTERM_STATE_DIR="$SD" AGTERM_CONTROL_SOCKET="$SOCK" "$FIXED/Contents/MacOS/agterm" > "$SD.exec.log" 2>&1 &
            PID=$!
            ;;
        open-url) "${SCRUB[@]}" open -n -a "$FIXED" --env AGTERM_STATE_DIR="$SD" --env AGTERM_CONTROL_SOCKET="$SOCK" "$2" ;;
    esac
    if [ -z "$PID" ]; then
        for _ in $(seq 1 75); do
            PID=$(app_pids | head -1)
            [ -n "$PID" ] && break
            sleep 0.2
        done
    fi
}

observe() {
    local deadline=$1 t0 now w
    WIN_AT=-
    SOCK_AT=-
    ALIVE=yes
    t0=$(date +%s)
    while :; do
        now=$(($(date +%s) - t0))
        if [ -z "$PID" ] || ! kill -0 "$PID" 2> /dev/null; then
            ALIVE=no
            break
        fi
        if [ "$WIN_AT" = - ]; then
            w=$("$WINCTL" "$PID" | cut -d' ' -f"$WINFIELD")
            [ "${w:-0}" -gt 0 ] && WIN_AT=$now
        fi
        if [ "$SOCK_AT" = - ] && ctl version --socket "$SOCK" > /dev/null 2>&1; then SOCK_AT=$now; fi
        [ "$WIN_AT" != - ] && [ "$SOCK_AT" != - ] && break
        [ "$now" -ge "$deadline" ] && break
        sleep 1
    done
}

appkit_log() {
    perl -e 'alarm shift; exec @ARGV' 90 log show --style compact --start "$LAUNCHED_AT" --predicate "processID == $PID" 2> /dev/null |
        grep -iE 'PersistentState|ApplePersistenceIgnoreState|reopenWindows|restor' | head -20 > "$1"
}

trial() {
    local mode=$1 tag=$2 verdict file
    new_sd
    saved_state "before $ARM $mode $tag"
    launch "$mode"
    env_check "$mode"
    observe "$WAIT"
    file=$OUT/trials/$ARM-$mode-$tag
    if [ "$ALIVE" = yes ]; then
        "$WINCTL" "$PID" > "$file.winctl"
        [ "$SOCK_AT" != - ] && ctl window list --json --socket "$SOCK" > "$file.windows.json" 2>&1
        appkit_log "$file.appkit.log"
    fi
    if [ "$ALIVE" = yes ] && [ "$WIN_AT" != - ] && [ "$SOCK_AT" != - ]; then
        verdict=window+socket
    elif [ "$ALIVE" = yes ] && [ "$WIN_AT" = - ] && [ "$SOCK_AT" = - ]; then
        verdict=hang
    else
        verdict=other
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ARM" "$mode" "$tag" "$ALIVE" "$WIN_AT" "$SOCK_AT" "$verdict" "$(tr ' ' / < "$file.winctl" 2> /dev/null)" >> "$RESULTS"
    log "$ARM $mode $tag: alive=$ALIVE window_at=$WIN_AT socket_at=$SOCK_AT -> $verdict (saved-state files before: $STATE_FILES)"
    kill_all
}

# Fails the run if the launched process inherited anything from the live terminal beyond the two variables set.
env_check() {
    local leaked
    [ -n "$PID" ] || return 0
    leaked=$(ps eww -p "$PID" -o command= | tr ' ' '\n' | grep -E '^(AGTERM_|GHOSTTY_|CLAUDE|CODEX|ZMX|__CFBundleIdentifier=)' |
        grep -vE "^(AGTERM_STATE_DIR=$SD|AGTERM_CONTROL_SOCKET=$SOCK)\$")
    if [ -n "$leaked" ]; then
        log "FAIL $1 launch inherited: $(echo "$leaked" | cut -d= -f1 | paste -sd' ' -)"
        exit 1
    fi
    echo "$1 pid $PID: clean" >> "$OUT/env-check.log"
}

# Quits the running instance the way a user's last-window close does, which discards AppKit's saved state.
clean_quit() {
    local id
    id=$(ctl window list --json --socket "$SOCK" | jq -r '.result.windows[] | select(.open) | .id' | head -1)
    ctl window close "$id" --socket "$SOCK" > /dev/null 2>&1
    for _ in $(seq 1 50); do
        kill -0 "$PID" 2> /dev/null || return 0
        sleep 0.2
    done
    log "WARN the instance did not exit after its last window closed"
    return 1
}

smoke_result() {
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$SMOKE"
    log "smoke $1: $2 - $3"
}

lower() { tr '[:upper:]' '[:lower:]'; }

# Two windows: W1 holds the selected session A and an unselected B, W2 is created last and so saved frontmost.
# The seed instance is SIGKILLed (a clean quit raises the quit alert) and each cold launch starts from that.
smoke() {
    local s k w1 w2 a b expect folder real tree windows found hits id
    stage patched
    new_sd
    s=$SD
    k=$SOCK
    launch open-g
    observe 60
    if [ "$WIN_AT" = - ] || [ "$SOCK_AT" = - ]; then
        smoke_result setup fail "seed instance did not come up (window_at=$WIN_AT socket_at=$SOCK_AT)"
        return
    fi
    w1=$(ctl window list --json --socket "$k" | jq -r '.result.windows[] | select(.active) | .id')
    a=$(ctl tree --json --socket "$k" | jq -r '.result.tree.workspaces[].sessions[] | select(.active) | .id')
    b=$(ctl session new --no-select --json --socket "$k" | jq -r '.result.id')
    w2=$(ctl window new --json --socket "$k" | jq -r '.result.id')
    sleep 3
    ctl window list --json --socket "$k" > "$OUT/smoke-seed-windows.json"
    ctl tree --json --window "$w1" --socket "$k" > "$OUT/smoke-seed-tree-w1.json"
    log "smoke seed: W1=$w1 A=$a B=$b W2=$w2"
    kill_all
    cp "$s/windows.json" "$OUT/smoke-windows-before-link.json"
    if [ "$(jq -r '.frontmost' "$s/windows.json" | lower)" != "$(echo "$w2" | lower)" ] || ! grep -rqi "$b" "$s/windows"; then
        smoke_result setup fail "saved state does not hold W2 frontmost and B (see smoke-windows-before-link.json)"
        return
    fi
    smoke_result setup pass "W1 $w1 holds selected A $a and unselected B $b; W2 $w2 saved frontmost; instance SIGKILLed"

    SD=$s
    SOCK=$k
    launch open-url "agterm://session/$b"
    observe 60
    found=no
    for _ in $(seq 1 20); do
        windows=$(ctl window list --json --socket "$k" 2> /dev/null)
        tree=$(ctl tree --json --window "$w1" --socket "$k" 2> /dev/null)
        if [ "$(echo "$windows" | jq -r '.result.windows[] | select(.active) | .id' | lower)" = "$(echo "$w1" | lower)" ] &&
            [ "$(echo "$tree" | jq -r '.result.tree.workspaces[].sessions[] | select(.active) | .id' | lower)" = "$(echo "$b" | lower)" ] &&
            [ "$(echo "$windows" | jq '[.result.windows[] | select(.open)] | length')" = 2 ]; then
            found=yes
            break
        fi
        sleep 0.5
    done
    echo "$windows" > "$OUT/smoke-link-windows.json"
    echo "$tree" > "$OUT/smoke-link-tree-w1.json"
    if [ "$found" = yes ]; then
        smoke_result cold-link pass "agterm://session/<B> selected B in W1 and brought W1 to the front; both windows open (window_at=${WIN_AT}s socket_at=${SOCK_AT}s)"
    else
        smoke_result cold-link fail "window_at=$WIN_AT socket_at=$SOCK_AT; see smoke-link-*.json"
    fi
    sleep 2
    kill_all

    cp "$s/windows.json" "$OUT/smoke-windows-before-folder.json"
    expect=$(jq -r '.frontmost' "$s/windows.json")
    folder=$ROOT/folder-open
    mkdir -p "$folder"
    real=$(cd "$folder" && pwd -P)
    SD=$s
    SOCK=$k
    launch open-url "$folder"
    observe 60
    found=no
    for _ in $(seq 1 20); do
        windows=$(ctl window list --json --socket "$k" 2> /dev/null)
        hits=""
        for id in $(echo "$windows" | jq -r '.result.windows[] | select(.open) | .id'); do
            if ctl tree --json --window "$id" --socket "$k" 2> /dev/null |
                jq -e --arg f "$folder" --arg r "$real" '[.result.tree.workspaces[].sessions[] | select(.cwd == $f or .cwd == $r)] | length > 0' > /dev/null; then
                hits="$hits $id"
            fi
        done
        if [ "$(echo $hits | lower)" = "$(echo "$expect" | lower)" ] &&
            [ "$(echo "$windows" | jq -r '.result.windows[] | select(.active) | .id' | lower)" = "$(echo "$expect" | lower)" ] &&
            [ "$(echo "$windows" | jq '[.result.windows[] | select(.open)] | length')" = 2 ]; then
            found=yes
            break
        fi
        sleep 0.5
    done
    echo "$windows" > "$OUT/smoke-folder-windows.json"
    for id in $(echo "$windows" | jq -r '.result.windows[] | select(.open) | .id'); do
        ctl tree --json --window "$id" --socket "$k" > "$OUT/smoke-folder-tree-$id.json" 2>&1
    done
    if [ "$found" = yes ]; then
        smoke_result cold-folder pass "the folder opened as a new session in the saved frontmost window ($expect) only, which is in front; both windows open (window_at=${WIN_AT}s socket_at=${SOCK_AT}s)"
    else
        smoke_result cold-folder fail "expected window $expect, sessions at the folder in:${hits:- none}; see smoke-folder-*.json"
    fi
    kill_all
}

restore() {
    local f
    kill_all
    lsappinfo list 2> /dev/null | grep -q 'agterm.debug' && log "WARN a com.umputun.agterm.debug instance is registered at restore time"
    for f in "$FIXED" "$OLD"/*-agterm.app; do [ -e "$f" ] && "$LSREGISTER" -u "$f" 2> /dev/null; done
    for f in "$SS"/*; do [ -f "$f" ] && [ ! -e "$BACKUP/savedState/${f##*/}" ] && rm -f "$f"; done
    ditto "$BACKUP/savedState" "$SS"
    touch -r "$BACKUP/savedState" "$SS"
    saved_state "after restoring the backup"
    defaults delete "$BID" 2> /dev/null
    defaults import "$BID" "$BACKUP/prefs.plist"
    defaults export "$BID" "$BACKUP/prefs-after.plist"
    if cmp -s <(plutil -convert xml1 -o - "$BACKUP/prefs.plist") <(plutil -convert xml1 -o - "$BACKUP/prefs-after.plist"); then
        log "restored $BID preferences: identical to the backup"
    else
        log "WARN $BID preferences differ from the backup after restore"
    fi
    if diff -r "$BACKUP/savedState" "$SS" > /dev/null; then log "restored saved state: identical to the backup"; else log "WARN saved state differs from the backup"; fi
    for f in Caches HTTPStorages WebKit; do
        if [ -e "$HOME/Library/$f/$BID" ] && ! grep -qx "$f" "$BACKUP/library-before.txt"; then
            mv "$HOME/Library/$f/$BID" "$ROOT/moved-$f-$BID" && log "moved $f/$BID created by the run to $ROOT/moved-$f-$BID"
        fi
    done
    log "leftover processes from the staged bundle: $(pgrep -f "$ROOT/" | wc -l | tr -d ' ')"
    log "lsappinfo agterm.debug entries: $(lsappinfo list 2> /dev/null | grep -c 'agterm.debug')"
}

# ---- preparation ----
if [ -e "$BACKUP/prefs.plist" ]; then
    echo "$BACKUP already holds a backup from an earlier run; restore or move it first" >&2
    exit 1
fi
if lsappinfo list 2> /dev/null | grep -q 'agterm.debug'; then
    echo "another com.umputun.agterm.debug instance is running; it shares the saved-state domain" >&2
    exit 1
fi
session=$(ioreg -n Root -d1) # captured first: grep -q closing the pipe early fails the pipeline under pipefail
if [[ $session == *'"CGSSessionScreenIsLocked"=Yes'* ]]; then
    WINFIELD=2
    log "screen locked: a window counts from any layer-0 window of the pid, on-screen or not"
else
    WINFIELD=1
    log "screen unlocked: a window counts only when on screen"
fi
SS=$(saved_state_dir)
[ -n "$SS" ] && [ -d "$SS" ] || {
    echo "no saved-state directory mapped for $BID" >&2
    exit 1
}
log "saved-state directory for $BID: $SS"
ditto "$SS" "$BACKUP/savedState"
touch -r "$SS" "$BACKUP/savedState"
defaults export "$BID" "$BACKUP/prefs.plist" 2> /dev/null || plutil -create xml1 "$BACKUP/prefs.plist"
for f in Caches HTTPStorages WebKit; do [ -e "$HOME/Library/$f/$BID" ] && echo "$f"; done > "$BACKUP/library-before.txt"
trap restore EXIT
trap 'exit 130' INT TERM HUP
saved_state "pre-existing state, backed up"
log "user default ApplePersistenceIgnoreState: app=$(defaults read "$BID" ApplePersistenceIgnoreState 2>&1) global=$(defaults read -g ApplePersistenceIgnoreState 2>&1)"
log "scrubbed variables: $(printf '%s\n' "${SCRUB[@]}" | grep -vE '^(env|-u|PATH=.*)$' | paste -sd' ' -); PATH without live agterm.app entries"

for arm in master patched; do
    mkdir -p "$ROOT/arms/$arm"
    ditto -x -k "$ARTIFACTS/agterm-$arm.zip" "$ROOT/arms/$arm"
    zip=$(shasum -a 256 "$ARTIFACTS/agterm-$arm.zip" | cut -d' ' -f1)
    dylib=$(shasum -a 256 "$ROOT/arms/$arm/agterm.app/Contents/MacOS/agterm.debug.dylib" | cut -d' ' -f1)
    if grep -q "zip $zip" "$ARTIFACTS/hashes.txt" && grep -q "$dylib  agterm.debug.dylib" "$ARTIFACTS/hashes.txt"; then
        log "$arm: zip $zip and dylib $dylib match the workflow's hashes.txt"
    else
        log "FAIL $arm hashes do not match hashes.txt"
        exit 1
    fi
    codesign --verify --deep --strict "$ROOT/arms/$arm/agterm.app" 2>&1 | head -3 | tee -a "$OUT/ab.log"
done

# ---- clear whatever state an earlier Debug run left (backed up above, put back by restore) ----
for f in "$SS"/*; do [ -f "$f" ] && rm -f "$f"; done
saved_state "after clearing the pre-existing state"
[ "$STATE_FILES" -eq 0 ] || {
    log "FAIL could not clear the pre-existing saved state"
    exit 1
}
stage master

# ---- seed ----
new_sd
touch "$ROOT/seed-start"
sleep 1
launch "${SEED_MODE:-open-g}"
observe 60
log "seed launch: alive=$ALIVE window_at=$WIN_AT socket_at=$SOCK_AT"
if [ "$WIN_AT" = - ] || [ "$SOCK_AT" = - ]; then
    log "FAIL the seed launch did not show a window and a socket"
    exit 1
fi
for _ in $(seq 1 "${SEED_DWELL:-25}"); do
    sleep 1
    saved_state "seed dwell"
    [ "$STATE_FILES" -gt 0 ] && [ -z "${SEED_WRITTEN:-}" ] && SEED_WRITTEN=$(($(date +%s) - $(stat -f %m "$ROOT/seed-start"))) && log "seed: AppKit wrote saved state ${SEED_WRITTEN}s after launch"
done
saved_state "seed after the dwell, before SIGKILL"
kill_all
sleep 2
saved_state "after the seed SIGKILL"
log "saved-state files after the seed SIGKILL: $STATE_FILES"
older=$(find "$SS" -type f ! -newer "$ROOT/seed-start" | wc -l | tr -d ' ')
[ "$STATE_FILES" -gt 0 ] && [ "$older" -eq 0 ] || {
    log "FAIL saved state after the seed SIGKILL: $STATE_FILES file(s), $older not written by the seed"
    exit 1
}

# ---- A/B ----
for t in $(seq 1 "$TRIALS"); do for m in open-g exec; do trial "$m" "r$t"; done; done
stage patched
for t in $(seq 1 "$TRIALS"); do for m in open-g exec; do trial "$m" "r$t"; done; done
stage master
for m in open-g exec; do trial "$m" after-patched; done

smoke

column -t -s $'\t' "$RESULTS" | tee -a "$OUT/ab.log"
column -t -s $'\t' "$SMOKE" | tee -a "$OUT/ab.log"
bad=$(awk -F'\t' 'NR > 1 && (($1 == "master" && $7 != "hang") || ($1 == "patched" && $7 != "window+socket"))' "$RESULTS" | wc -l | tr -d ' ')
smokebad=$(awk -F'\t' 'NR > 1 && $2 != "pass"' "$SMOKE" | wc -l | tr -d ' ')
if [ "$bad" -ne 0 ] || [ "$smokebad" -ne 0 ]; then
    log "FAIL $bad A/B trial(s) off expectation, $smokebad smoke check(s) not passing"
    exit 1
fi
log "PASS every master trial hung, every patched trial showed a window and answered on its socket, smoke checks passed"
