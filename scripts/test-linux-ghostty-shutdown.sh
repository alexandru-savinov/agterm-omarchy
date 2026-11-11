#!/usr/bin/env bash
# Exercise the actual pinned queue, including its before-fix shutdown failure.
# Usage: ZIG=/path/to/zig ./scripts/test-linux-ghostty-shutdown.sh /path/to/ghostty-git
# The source repository must contain GHOSTTY_REV. Its checkout is never modified.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../linux/ghostty-resources.env
source "$ROOT/linux/ghostty-resources.env"
SOURCE="${1:?usage: test-linux-ghostty-shutdown.sh /path/to/ghostty-git}"
ZIG="${ZIG:-$(command -v zig || true)}"
[[ -x "$ZIG" && "$("$ZIG" version)" == 0.16.0 ]] || {
  echo "Zig 0.16.0 is required" >&2
  exit 1
}
[[ "$(git -C "$SOURCE" rev-parse "$GHOSTTY_REV^{commit}")" == "$GHOSTTY_REV" ]]
WORK="$(mktemp -d "${TMPDIR:-/tmp}/agterm-ghostty-shutdown.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/source"
git -C "$SOURCE" archive "$GHOSTTY_REV" | tar -xf - -C "$WORK/source"
cp "$WORK/source/src/datastruct/blocking_queue.zig" "$WORK/original-queue.zig"
for patch in ghostty-embedded-opengl.patch ghostty-embedded-input-handoff.patch ghostty-lib-resources.patch; do
  git -C "$WORK/source" apply --check "$ROOT/linux/patches/$patch"
  git -C "$WORK/source" apply "$ROOT/linux/patches/$patch"
done
export ZIG_GLOBAL_CACHE_DIR="$WORK/zig-global-cache"
export ZIG_LOCAL_CACHE_DIR="$WORK/zig-local-cache"

cat > "$WORK/source/src/shutdown_regression.zig" <<'ZIG'
const std = @import("std");
const Queue = @import("datastruct/blocking_queue.zig").BlockingQueue(u64, 64);
// The original queue has neither API. These adapters preserve its unmodified
// push path so the same assertion fails behaviorally, rather than at compile time.
const Cancellation = if (@hasDecl(Queue, "Cancellation")) Queue.Cancellation else struct {};

fn send(q: *Queue, io: std.Io, value: u64, token: ?*Cancellation) Queue.Size {
    if (@hasDecl(Queue, "pushWithCancellation"))
        return q.pushWithCancellation(io, value, .forever, token);
    return q.push(io, value, .forever);
}

fn cancel(q: *Queue, io: std.Io, token: *Cancellation) void {
    if (@hasDecl(Queue, "cancel")) q.cancel(io, token);
}

const Writer = struct {
    queue: *Queue,
    token: ?*Cancellation,
    value: u64,
    result: Queue.Size = undefined,
    done: std.Io.Event = .unset,

    fn run(self: *Writer, io: std.Io) void {
        self.result = send(self.queue, io, self.value, self.token);
        self.done.set(io);
    }

    fn completed(self: *Writer, io: std.Io) bool {
        const deadline = (std.Io.Timeout{ .duration = .{
            .raw = .fromSeconds(1),
            .clock = .awake,
        } }).toDeadline(io);
        while (!self.done.isSet()) {
            self.done.waitTimeout(io, deadline) catch {};
            if (deadline.deadline.untilNow(io).raw.nanoseconds >= 0)
                return self.done.isSet();
        }
        return true;
    }
};

fn waiters(q: *Queue, io: std.Io, count: usize, signals_consumed: bool) bool {
    for (0..1000) |_| {
        q.mutex.lockUncancelable(io);
        // Checking the pinned Zig condition's outstanding signals proves that
        // the unrelated producer consumed the broadcast and re-entered its wait.
        // A sleep alone would allow a false pass if that producer ran after pop.
        const ready = q.not_full_waiters == count and
            (!signals_consumed or q.cond_not_full.state.load(.acquire).signals == 0);
        q.mutex.unlock(io);
        if (ready) return true;
        std.Io.sleep(io, .fromMilliseconds(1), .awake) catch {};
    }
    return false;
}

fn fill(q: *Queue, io: std.Io) !void {
    for (0..64) |i| try std.testing.expectEqual(i + 1, q.push(io, i, .instant));
}

fn drain(q: *Queue, io: std.Io) void {
    while (q.pop(io) != null) {}
}

test "shutdown cancellation releases a full mailbox before any consumer drain" {
    const io = std.testing.io;
    var q: Queue = .{};
    var closing: Cancellation = .{};
    try fill(&q, io);
    var writer: Writer = .{ .queue = &q, .token = &closing, .value = 100 };
    const thread = try std.Thread.spawn(.{}, Writer.run, .{ &writer, io });
    // Even the expected failing baseline exits normally: release the fixture
    // writer by draining only AFTER recording the assertion, then join it.
    defer {
        drain(&q, io);
        thread.join();
    }
    try std.testing.expect(waiters(&q, io, 1, false));
    cancel(&q, io, &closing);
    const completed_before_drain = writer.completed(io);
    if (!completed_before_drain)
        std.debug.print("shutdown negative control: producer blocked until fixture drain\n", .{});
    try std.testing.expect(completed_before_drain);
    try std.testing.expectEqual(0, writer.result);

    // Cancellation must not consume or reorder any already accepted peer payload.
    for (0..64) |i| try std.testing.expectEqual(i, q.pop(io).?);
    try std.testing.expectEqual(null, q.pop(io));
    try std.testing.expectEqual(0, send(&q, io, 101, &closing));
    var live: Cancellation = .{};
    try std.testing.expectEqual(1, send(&q, io, 102, &live));
    try std.testing.expectEqual(2, send(&q, io, 103, null));
    try std.testing.expectEqual(102, q.pop(io).?);
    try std.testing.expectEqual(103, q.pop(io).?);
}

test "shutdown cancellation preserves blocked peer and app-wide producers" {
    const io = std.testing.io;
    for ([_]bool{ true, false }) |use_peer_token| {
        var q: Queue = .{};
        var closing: Cancellation = .{};
        var peer: Cancellation = .{};
        try fill(&q, io);
        var closing_writer: Writer = .{ .queue = &q, .token = &closing, .value = 100 };
        const closing_thread = try std.Thread.spawn(.{}, Writer.run, .{ &closing_writer, io });
        defer {
            drain(&q, io);
            closing_thread.join();
        }
        var peer_writer: Writer = .{
            .queue = &q,
            .token = if (use_peer_token) &peer else null,
            .value = 200,
        };
        const peer_thread = try std.Thread.spawn(.{}, Writer.run, .{ &peer_writer, io });
        defer {
            drain(&q, io);
            peer_thread.join();
        }
        try std.testing.expect(waiters(&q, io, 2, false));
        cancel(&q, io, &closing);
        try std.testing.expect(closing_writer.completed(io));
        try std.testing.expectEqual(0, closing_writer.result);
        try std.testing.expect(waiters(&q, io, 1, true));
        try std.testing.expect(!peer_writer.done.isSet());
        try std.testing.expectEqual(0, q.pop(io).?);
        try std.testing.expect(peer_writer.completed(io));
        try std.testing.expectEqual(64, peer_writer.result);
        for (1..64) |i| try std.testing.expectEqual(i, q.pop(io).?);
        try std.testing.expectEqual(200, q.pop(io).?);
        try std.testing.expectEqual(null, q.pop(io));
    }
}
ZIG

# Bound compiler and fixture execution; a hang/compile error cannot count as the
# expected negative. Each invocation has its own two-job compiler limit.
run_test() {
  python3 - "$ZIG" "$WORK/source/src/shutdown_regression.zig" "$@" <<'PY'
import subprocess
import sys
try:
    result = subprocess.run([sys.argv[1], "test", "-j2", sys.argv[2], *sys.argv[3:]], timeout=90)
except subprocess.TimeoutExpired:
    print("shutdown regression timed out", file=sys.stderr)
    sys.exit(124)
sys.exit(result.returncode)
PY
}

echo "Pinned Ghostty source: $GHOSTTY_REV"
python3 - "$ROOT/linux/patches/ghostty-embedded-opengl.patch" <<'PY'
import hashlib
import pathlib
import sys
print("Patch SHA256: " + hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
run_test
cp "$WORK/original-queue.zig" "$WORK/source/src/datastruct/blocking_queue.zig"
if run_test --test-filter 'shutdown cancellation releases a full mailbox' > "$WORK/negative.log" 2>&1; then
  cat "$WORK/negative.log"
  echo "ERROR: original queue unexpectedly passed the shutdown assertion" >&2
  exit 1
fi
cat "$WORK/negative.log"
python3 - "$WORK/negative.log" <<'PY'
import pathlib
import sys
log = pathlib.Path(sys.argv[1]).read_text()
assert "shutdown negative control: producer blocked until fixture drain" in log, log
assert "FAIL (TestUnexpectedResult)" in log, log
assert "1 failed" in log, log
assert "shutdown regression timed out" not in log, log
print("PASS: patched queue passes; original pinned queue fails the bounded shutdown assertion")
PY
