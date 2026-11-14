import CGtk
import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Linux hook launcher child I/O and completion", .serialized)
struct LinuxHookLauncherTests {
    init() { signal(SIGPIPE, SIG_IGN) }

    @Test("an event over 64 KiB reaches the hook's stdin whole, newline-terminated")
    func largeEventBytes() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let event = largeEvent()
        let run = try launch("cat > '\(directory)/event'", event: event)
        #expect(run.exits == [0])
        #expect(run.deliveryFailures.isEmpty)
        let received = try #require(FileManager.default.contents(atPath: directory + "/event"))
        #expect(received.count > 64 * 1024)
        #expect(received.count == (try JSONEncoder().encode(event)).count + 1)
        #expect(received.last == UInt8(ascii: "\n"))
        #expect(try JSONDecoder().decode(ControlEvent.self, from: received.dropLast()) == event)
    }

    @Test("a hook that closes stdin early swallows EPIPE and exits once")
    func earlyStdinClose() throws {
        let run = try launch("exec 0<&-; exit 0", event: largeEvent())
        #expect(run.exits == [0])
        #expect(run.deliveryFailures.isEmpty)
    }

    @Test("a nonzero hook reports its status once")
    func nonzeroExit() throws {
        let run = try launch("exit 3", event: smallEvent())
        #expect(run.exits == [3])
        #expect(run.deliveryFailures.isEmpty)
    }

    @Test("a launch failure throws and never reports an exit")
    func launchThrows() {
        let launcher = LinuxHookLauncher(shellPath: "/definitely/missing/agterm-sh", socketPath: { "/tmp/agterm-test.sock" })
        let run = Run()
        do {
            _ = try launcher.launch(entry: entry("true"), event: smallEvent(),
                                    onDeliveryFailure: { run.deliveryFailures.append($0) },
                                    onExit: { run.exits.append($0) })
            Issue.record("a missing shell unexpectedly launched")
        } catch {
            #expect(!error.localizedDescription.isEmpty)
        }
        pump(for: 0.2)
        #expect(run.exits.isEmpty)
        #expect(run.deliveryFailures.isEmpty)
    }

    @Test("the hook sees the restored GDK environment, Linux PATH and event variables")
    func environment() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let run = try launch("env -0 > '\(directory)/env'", event: smallEvent())
        #expect(run.exits == [0])
        let data = try #require(FileManager.default.contents(atPath: directory + "/env"))
        let pairs = data.split(separator: 0).map { String(decoding: $0, as: UTF8.self) }
        let child = Dictionary(pairs.compactMap { pair -> (String, String)? in
            guard let equals = pair.firstIndex(of: "=") else { return nil }
            return (String(pair[..<equals]), String(pair[pair.index(after: equals)...]))
        }) { first, _ in first }
        for (name, restored) in gdkEnvironment.childRestore {
            #expect(child[name] == restored)
        }
        #expect(child["AGT_EVENT_KIND"] == ControlEventKind.notify.rawValue)
        #expect(child["AGT_SESSION_ID"] == "session")
        #expect(child["AGT_SOCKET"] == "/tmp/agterm-test.sock")
        let parent = gdkEnvironment.restoringChildEnvironment(ProcessInfo.processInfo.environment)
        let home = parent["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
        let bundled = LinuxCommandPath.bundledCLIDirectory
        #expect(child["HOME"] == parent["HOME"])
        #expect(child["PATH"] == LinuxCommandPath.widened(
            parent["PATH"], bundledCLIDirectory: bundled, homeDirectory: home))
        let paths = try #require(child["PATH"]).split(separator: ":").map(String.init)
        #expect(paths.contains((home as NSString).appendingPathComponent(".local/bin")))
        if let bundled { #expect(paths.first == bundled) }
    }

    @Test("the stdin pipe is closed in agterm once the hook exits")
    func pipeClosed() throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let run = try launch("readlink /proc/self/fd/0 > '\(directory)/stdin'; cat > /dev/null", event: largeEvent())
        #expect(run.exits == [0])
        let link = try #require(try? String(contentsOfFile: directory + "/stdin", encoding: .utf8))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        #expect(link.hasPrefix("pipe:["))
        pump(for: 1) { descriptors(linkingTo: link).isEmpty }
        #expect(descriptors(linkingTo: link).isEmpty)
    }

    @MainActor
    private final class Run {
        var exits: [Int32] = []
        var deliveryFailures: [String] = []
    }

    /// Pumps until the first exit, then a further 300 ms so a duplicate exit would be seen.
    private func launch(_ command: String, event: ControlEvent) throws -> Run {
        let launcher = LinuxHookLauncher(socketPath: { "/tmp/agterm-test.sock" })
        let run = Run()
        _ = try launcher.launch(entry: entry(command), event: event,
                                onDeliveryFailure: { run.deliveryFailures.append($0) },
                                onExit: { run.exits.append($0) })
        pump(for: 10) { !run.exits.isEmpty }
        pump(for: 0.3)
        return run
    }
}

private func entry(_ command: String) -> HookEntry {
    HookEntry(identity: HookIdentity(kind: .notify, command: command), line: 1)
}

private func smallEvent() -> ControlEvent {
    ControlEvent(seq: 1, ts: 0, kind: .notify, session: "session", payload: ControlEventPayload(body: "small"))
}

private func largeEvent() -> ControlEvent {
    ControlEvent(seq: 2, ts: 0, kind: .notify, session: "session",
                 payload: ControlEventPayload(body: String(repeating: "x", count: 100 * 1024)))
}

private func pump(for seconds: TimeInterval, until done: () -> Bool = { false }) {
    let end = Date().addingTimeInterval(seconds)
    while Date() < end, !done() {
        if g_main_context_iteration(nil, 0) == 0 { usleep(5000) }
    }
}

private func makeDirectory() throws -> String {
    var template = Array("/tmp/agterm-hook-test-XXXXXX".utf8CString)
    guard mkdtemp(&template) != nil else { throw POSIXError(.init(rawValue: errno) ?? .EIO) }
    return String(decoding: template.dropLast().map { UInt8(bitPattern: $0) }, as: UTF8.self)
}

private func descriptors(linkingTo target: String) -> [String] {
    let names = (try? FileManager.default.contentsOfDirectory(atPath: "/proc/self/fd")) ?? []
    return names.filter { (try? FileManager.default.destinationOfSymbolicLink(atPath: "/proc/self/fd/" + $0)) == target }
}
