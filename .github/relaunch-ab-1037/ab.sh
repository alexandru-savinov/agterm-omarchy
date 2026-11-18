#!/usr/bin/env bash
# A/B for umputun/agterm Discussion #1037 on a hosted macOS runner, plus the cold link and folder smoke checks.
#
#   ab.sh <arms dir holding master/agterm.app and patched/agterm.app> <output dir>
#
# Both arms launch from ONE bundle path, swapped in between, so AppKit sees the same app on the same saved state.
# The stale state is made once by the master arm: a launch that shows its window, ~25 s for AppKit to write its
# state, then SIGKILL. Order is master -> patched -> master, with nothing quitting cleanly in between. No launch
# argument and no `defaults write` is used anywhere. A window is counted from the window server by pid
# (winctl), never through System Events or a second `open`, which could deliver the reopen that masks the hang.
set -uo pipefail

ARMS=$1
OUT=$2
WINCTL=${WINCTL:?WINCTL must name the compiled winctl}
TRIALS=${TRIALS:-3}
WAIT=${WAIT:-30}
ROOT=/private/tmp/ab1037 # what an open-launched process reports, so the pgrep patterns match
FIXED=$ROOT/app/agterm.app
OLD=$ROOT/old
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
mkdir -p "$ROOT/app" "$OLD" "$OUT/trials"
RESULTS=$OUT/results.tsv
SMOKE=$OUT/smoke.tsv
printf 'arm\tmode\ttrial\talive\twindow_at_s\tsocket_at_s\tverdict\n' > "$RESULTS"
printf 'check\tresult\tdetail\n' > "$SMOKE"
staged=0
sdn=0

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$OUT/ab.log"; }
ctl() { perl -e 'alarm shift; exec @ARGV' 10 "$FIXED/Contents/MacOS/agtermctl" "$@"; }
app_pids() { pgrep -f "$FIXED/Contents/MacOS/agterm( |\$)"; }

kill_all() {
    pkill -KILL -f "$FIXED/Contents/" 2>/dev/null
    for _ in $(seq 1 50); do
        pgrep -f "$FIXED/Contents/" >/dev/null || return 0
        sleep 0.2
    done
    log "WARN processes from $FIXED still alive: $(pgrep -fl "$FIXED/Contents/" | tr '\n' ';')"
}

stage() {
    kill_all
    staged=$((staged + 1))
    [ -e "$FIXED" ] && mv "$FIXED" "$OLD/$staged-agterm.app"
    ditto "$ARMS/$1/agterm.app" "$FIXED"
    "$LSREGISTER" -f "$FIXED"
    ARM=$1
    log "staged $1 at $FIXED: $(grep -a -c ApplePersistenceIgnoreState "$FIXED/Contents/MacOS/agterm.debug.dylib") dylib match(es) for ApplePersistenceIgnoreState"
}

new_sd() {
    sdn=$((sdn + 1))
    SD=$ROOT/s$sdn
    mkdir -p "$SD/windows" # skips the first-run welcome alert
}

# Every saved-state file changed since the job started, with its hash, so each launch shows the state it met.
saved_state() {
    local label=$1 n=0 f
    {
        echo "== $label ($(date -u +%H:%M:%S))"
        for d in "$HOME/Library/Daemon Containers" "$HOME/Library/Saved Application State"; do
            [ -d "$d" ] || continue
            while IFS= read -r f; do
                n=$((n + 1))
                printf '%s  %s  %s\n' "$(stat -f '%Sm' -t '%H:%M:%S' "$f")" "$(shasum -a 256 "$f" | cut -c1-16)" "$f"
            done < <(find "$d" -path '*.savedState/*' -type f -newer "$ROOT/start" 2>>"$OUT/savedstate-find-errors.log")
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
        open-g) open -n -g --env AGTERM_STATE_DIR="$SD" "$FIXED" ;;
        open-fg) open -n --env AGTERM_STATE_DIR="$SD" "$FIXED" ;;
        exec)
            AGTERM_STATE_DIR="$SD" "$FIXED/Contents/MacOS/agterm" > "$SD.exec.log" 2>&1 &
            PID=$!
            ;;
        open-url) open -n -a "$FIXED" --env AGTERM_STATE_DIR="$SD" "$2" ;;
    esac
    if [ -z "$PID" ]; then
        for _ in $(seq 1 75); do
            PID=$(app_pids | head -1)
            [ -n "$PID" ] && break
            sleep 0.2
        done
    fi
}

# Polls once a second until both a window and a control-socket answer are seen, or the deadline passes.
observe() {
    local deadline=$1 t0 now w
    WIN_AT=-
    SOCK_AT=-
    ALIVE=yes
    t0=$(date +%s)
    while :; do
        now=$(($(date +%s) - t0))
        if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
            ALIVE=no
            break
        fi
        if [ "$WIN_AT" = - ]; then
            w=$("$WINCTL" "$PID" | cut -d' ' -f1)
            [ "${w:-0}" -gt 0 ] && WIN_AT=$now
        fi
        if [ "$SOCK_AT" = - ] && ctl version --socket "$SD/agterm.sock" > /dev/null 2>&1; then SOCK_AT=$now; fi
        [ "$WIN_AT" != - ] && [ "$SOCK_AT" != - ] && break
        [ "$now" -ge "$deadline" ] && break
        sleep 1
    done
}

# The AppKit restore lines for one launch, from the unified log.
appkit_log() {
    perl -e 'alarm shift; exec @ARGV' 90 log show --style compact --start "$LAUNCHED_AT" --predicate "processID == $PID" 2>/dev/null |
        grep -iE 'PersistentState|ApplePersistenceIgnoreState|reopenWindows|restor' | head -20 > "$1"
}

trial() {
    local mode=$1 tag=$2 verdict file
    new_sd
    saved_state "before $ARM $mode $tag"
    launch "$mode"
    observe "$WAIT"
    file=$OUT/trials/$ARM-$mode-$tag
    if [ "$ALIVE" = yes ]; then
        "$WINCTL" "$PID" > "$file.winctl"
        [ "$SOCK_AT" != - ] && ctl window list --json --socket "$SD/agterm.sock" > "$file.windows.json" 2>&1
        appkit_log "$file.appkit.log"
    fi
    if [ "$ALIVE" = yes ] && [ "$WIN_AT" != - ] && [ "$SOCK_AT" != - ]; then
        verdict=window+socket
    elif [ "$ALIVE" = yes ] && [ "$WIN_AT" = - ] && [ "$SOCK_AT" = - ]; then
        verdict=hang
    else
        verdict=other
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ARM" "$mode" "$tag" "$ALIVE" "$WIN_AT" "$SOCK_AT" "$verdict" >> "$RESULTS"
    log "$ARM $mode $tag: alive=$ALIVE window_at=$WIN_AT socket_at=$SOCK_AT -> $verdict (state files seen: $STATE_FILES)"
    kill_all
}

smoke_result() {
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$SMOKE"
    log "smoke $1: $2 - $3"
}

lower() { tr '[:upper:]' '[:lower:]'; }

# Two windows: W1 holds the selected session A and an unselected B, W2 is created last and so frontmost.
# The instance is then SIGKILLed (a clean quit raises the quit alert), and the cold launches start from that.
smoke() {
    local s w1 w2 a b expect folder real tree windows found hits id
    stage patched
    new_sd
    s=$SD
    launch open-g
    observe 60
    if [ "$WIN_AT" = - ] || [ "$SOCK_AT" = - ]; then
        smoke_result setup fail "seed instance did not come up (window_at=$WIN_AT socket_at=$SOCK_AT)"
        return
    fi
    w1=$(ctl window list --json --socket "$s/agterm.sock" | jq -r '.result.windows[] | select(.active) | .id')
    a=$(ctl tree --json --socket "$s/agterm.sock" | jq -r '.result.tree.workspaces[].sessions[] | select(.active) | .id')
    b=$(ctl session new --no-select --json --socket "$s/agterm.sock" | jq -r '.result.id')
    w2=$(ctl window new --json --socket "$s/agterm.sock" | jq -r '.result.id')
    sleep 3
    ctl window list --json --socket "$s/agterm.sock" > "$OUT/smoke-seed-windows.json"
    ctl tree --json --window "$w1" --socket "$s/agterm.sock" > "$OUT/smoke-seed-tree-w1.json"
    log "smoke seed: W1=$w1 A=$a B=$b W2=$w2"
    kill_all
    cp "$s/windows.json" "$OUT/smoke-windows-before-link.json"
    if [ "$(jq -r '.frontmost' "$s/windows.json" | lower)" != "$(echo "$w2" | lower)" ] || ! grep -rqi "$b" "$s/windows"; then
        smoke_result setup fail "saved state does not hold W2 frontmost and B (see smoke-windows-before-link.json)"
        return
    fi
    smoke_result setup pass "W1 $w1 holds selected A $a and unselected B $b; W2 $w2 saved frontmost; instance SIGKILLed"

    # cold link: B selected in W1, and W1 in front, with both windows still open
    SD=$s
    launch open-url "agterm://session/$b"
    observe 60
    found=no
    for _ in $(seq 1 20); do
        windows=$(ctl window list --json --socket "$s/agterm.sock" 2>/dev/null)
        tree=$(ctl tree --json --window "$w1" --socket "$s/agterm.sock" 2>/dev/null)
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
        smoke_result cold-link pass "agterm://session/$b selected B in W1, W1 frontmost, 2 windows open (window_at=$WIN_AT socket_at=$SOCK_AT)"
    else
        smoke_result cold-link fail "window_at=$WIN_AT socket_at=$SOCK_AT; see smoke-link-*.json"
    fi
    sleep 2
    kill_all

    # cold folder: a new session at the folder in the saved frontmost window, and nowhere else
    cp "$s/windows.json" "$OUT/smoke-windows-before-folder.json"
    expect=$(jq -r '.frontmost' "$s/windows.json")
    folder=$ROOT/folder-open
    mkdir -p "$folder"
    real=$(cd "$folder" && pwd -P)
    SD=$s
    launch open-url "$folder"
    observe 60
    found=no
    for _ in $(seq 1 20); do
        windows=$(ctl window list --json --socket "$s/agterm.sock" 2>/dev/null)
        hits=""
        for id in $(echo "$windows" | jq -r '.result.windows[] | select(.open) | .id'); do
            if ctl tree --json --window "$id" --socket "$s/agterm.sock" 2>/dev/null |
                jq -e --arg f "$folder" --arg r "$real" '[.result.tree.workspaces[].sessions[] | select(.cwd == $f or .cwd == $r)] | length > 0' > /dev/null; then
                hits="$hits $id"
            fi
        done
        if [ "$(echo $hits | lower)" = "$(echo "$expect" | lower)" ] &&
            [ "$(echo "$windows" | jq '[.result.windows[] | select(.open)] | length')" = 2 ]; then
            found=yes
            break
        fi
        sleep 0.5
    done
    echo "$windows" > "$OUT/smoke-folder-windows.json"
    for id in $(echo "$windows" | jq -r '.result.windows[] | select(.open) | .id'); do
        ctl tree --json --window "$id" --socket "$s/agterm.sock" > "$OUT/smoke-folder-tree-$id.json" 2>&1
    done
    if [ "$found" = yes ]; then
        smoke_result cold-folder pass "$folder opened as a session in saved frontmost window $expect only, 2 windows open (window_at=$WIN_AT socket_at=$SOCK_AT)"
    else
        smoke_result cold-folder fail "expected window $expect, sessions at the folder in:${hits:- none}; see smoke-folder-*.json"
    fi
    kill_all
}

touch "$ROOT/start"
log "user default ApplePersistenceIgnoreState: app=$(defaults read com.umputun.agterm.debug ApplePersistenceIgnoreState 2>&1) global=$(defaults read -g ApplePersistenceIgnoreState 2>&1)"

stage master
# AppKit writes the app-global record on its own schedule, so the seed tries each launch style in turn and keeps the
# first that leaves saved state; "+resize" also moves and resizes the window over the control socket meanwhile.
seeded=no
for seed in ${SEED_MODES:-open-fg exec open-g+resize}; do
    new_sd
    saved_state "before seed $seed"
    launch "${seed%+resize}"
    observe 60
    log "seed $seed: alive=$ALIVE window_at=$WIN_AT socket_at=$SOCK_AT"
    if [ "$WIN_AT" = - ] || [ "$SOCK_AT" = - ]; then
        log "seed $seed did not show a window and a socket"
        kill_all
        continue
    fi
    if [ "$seed" != "${seed%+resize}" ]; then
        id=$(ctl window list --json --socket "$SD/agterm.sock" | jq -r '.result.windows[0].id')
        ctl window resize "$id" --width 900 --height 600 --socket "$SD/agterm.sock" > /dev/null 2>&1
        ctl window move "$id" --x 120 --y 120 --socket "$SD/agterm.sock" > /dev/null 2>&1
    fi
    written=
    for i in $(seq 1 "${SEED_DWELL:-45}"); do
        sleep 1
        saved_state "seed $seed dwell ${i}s"
        if [ "$STATE_FILES" -gt 0 ] && [ -z "$written" ]; then
            written=$i
            log "seed $seed: saved state appeared ${i}s after the window"
        fi
        [ -n "$written" ] && [ $((i - written)) -ge 5 ] && break
    done
    kill -KILL "$PID"
    kill_all
    sleep 2
    saved_state "after seed $seed SIGKILL"
    log "saved-state files after the seed $seed SIGKILL: $STATE_FILES"
    if [ "$STATE_FILES" -gt 0 ]; then
        seeded=$seed
        break
    fi
done
if [ "$seeded" = no ]; then
    log "FAIL no seed style left saved state, so there is nothing stale to relaunch on"
    exit 1
fi
log "stale state made by the master seed ($seeded), SIGKILLed"

for t in $(seq 1 "$TRIALS"); do for m in open-g exec; do trial "$m" "r$t"; done; done
stage patched
for t in $(seq 1 "$TRIALS"); do for m in open-g exec; do trial "$m" "r$t"; done; done
stage master
for m in open-g exec; do trial "$m" after-patched; done

smoke

column -t -s $'\t' "$RESULTS" | tee -a "$OUT/ab.log"
column -t -s $'\t' "$SMOKE" | tee -a "$OUT/ab.log"
bad=$(awk -F'\t' 'NR > 1 && (($1 == "master" && $7 != "hang") || ($1 == "patched" && $7 != "window+socket"))' "$RESULTS" | wc -l)
smokebad=$(awk -F'\t' 'NR > 1 && $2 != "pass"' "$SMOKE" | wc -l)
if [ "$bad" -ne 0 ] || [ "$smokebad" -ne 0 ]; then
    log "FAIL $bad A/B trial(s) off expectation, $smokebad smoke check(s) not passing"
    exit 1
fi
log "PASS every master trial hung, every patched trial showed a window and answered on its socket, smoke checks passed"
