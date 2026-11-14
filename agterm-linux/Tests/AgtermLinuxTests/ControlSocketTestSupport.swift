import CGtk
import Foundation
import Glibc
import agtermCore
@testable import AgtermLinux

struct SocketTestError: Error {
    let call: String
    let code: Int32
}

/// A disposable directory holding one control socket path.
struct ControlSocketDirectory {
    let url: URL
    var socket: String { url.appendingPathComponent("agterm.sock").path }

    init() throws {
        url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    func remove() { try? FileManager.default.removeItem(at: url) }
}

/// A blocking client on one connected descriptor. `close()` must run after every thread using it is done.
struct ControlTestClient: Sendable {
    enum Reply: Equatable, Sendable {
        case line(Data)
        case closed(Data)
        case timedOut(Data)
    }

    let fd: Int32

    init(fd: Int32) { self.fd = fd }

    init(path: String) throws {
        let fd = socket(AF_UNIX, Int32(SOCK_STREAM.rawValue), 0)
        guard fd >= 0 else { throw SocketTestError(call: "socket", code: errno) }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = path.utf8CString
        withUnsafeMutablePointer(to: &addr.sun_path) { dst in
            dst.withMemoryRebound(to: CChar.self, capacity: bytes.count) { buf in
                bytes.withUnsafeBufferPointer { src in buf.update(from: src.baseAddress!, count: src.count) }
            }
        }
        let connected = withUnsafePointer(to: &addr) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Glibc.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else {
            let code = errno
            Glibc.close(fd)
            throw SocketTestError(call: "connect", code: code)
        }
        self.fd = fd
    }

    func close() { Glibc.close(fd) }

    @discardableResult
    func send(_ data: Data) -> Bool {
        data.withUnsafeBytes { raw in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return true }
            var offset = 0
            while offset < data.count {
                let count = Glibc.send(fd, base + offset, data.count - offset, Int32(MSG_NOSIGNAL))
                if count < 0, errno == EINTR { continue }
                guard count > 0 else { return false }
                offset += count
            }
            return true
        }
    }

    @discardableResult
    func send(_ text: String) -> Bool { send(Data(text.utf8)) }

    /// Reads up to the first newline, or until the peer closes or `timeout` passes.
    func readLine(timeout: TimeInterval) -> Reply {
        read(timeout: timeout, stopAtNewline: true)
    }

    /// Reads everything the peer sends until it closes, or until `timeout` passes.
    func readToClose(timeout: TimeInterval) -> Reply {
        read(timeout: timeout, stopAtNewline: false)
    }

    /// Reads exactly `byteCount` bytes unless the peer closes or `timeout` passes first.
    func read(byteCount: Int, timeout: TimeInterval) -> Data {
        let deadline = Date(timeIntervalSinceNow: timeout)
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 64 * 1024)
        while buffer.count < byteCount, deadline.timeIntervalSinceNow > 0 {
            var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            guard poll(&pfd, 1, 100) > 0 else { continue }
            let count = recv(fd, &chunk, min(chunk.count, byteCount - buffer.count), 0)
            if count < 0, errno == EINTR { continue }
            guard count > 0 else { break }
            buffer.append(contentsOf: chunk[0..<count])
        }
        return buffer
    }

    func response(timeout: TimeInterval) -> ControlResponse? {
        guard case .line(let data) = readLine(timeout: timeout) else { return nil }
        return try? JSONDecoder().decode(ControlResponse.self, from: data)
    }

    /// True once the peer has closed and nothing unread is left.
    var isClosedByPeer: Bool {
        var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
        guard poll(&pfd, 1, 0) > 0 else { return false }
        var byte: UInt8 = 0
        let count = recv(fd, &byte, 1, Int32(MSG_PEEK | MSG_DONTWAIT))
        return count == 0 || (count < 0 && errno == ECONNRESET)
    }

    private func read(timeout: TimeInterval, stopAtNewline: Bool) -> Reply {
        let deadline = Date(timeIntervalSinceNow: timeout)
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { return .timedOut(buffer) }
            var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, Int32(min(remaining * 1_000, 1_000).rounded(.up)))
            if ready < 0, errno == EINTR { continue }
            if ready == 0 { continue }
            let count = recv(fd, &chunk, chunk.count, 0)
            if count < 0, errno == EINTR { continue }
            guard count > 0 else { return .closed(buffer) }
            buffer.append(contentsOf: chunk[0..<count])
            if stopAtNewline, let newline = buffer.firstIndex(of: 0x0A) {
                return .line(buffer[buffer.startIndex..<newline])
            }
        }
    }
}

/// A connected AF_UNIX stream pair: `.0` for the code under test, `.1` for the test's peer.
func makeSocketPair() throws -> (Int32, Int32) {
    var fds: [Int32] = [-1, -1]
    guard socketpair(AF_UNIX, Int32(SOCK_STREAM.rawValue), 0, &fds) == 0 else {
        throw SocketTestError(call: "socketpair", code: errno)
    }
    return (fds[0], fds[1])
}

/// A value written by a worker thread and read by the test.
final class TestSlot<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Value?

    var value: Value? {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    func set(_ value: Value) {
        lock.lock()
        stored = value
        lock.unlock()
    }
}

/// Polls `done` without pumping GLib; false if it never held within `timeout`.
@discardableResult
func waitUntil(timeout: TimeInterval, _ done: () -> Bool) -> Bool {
    let deadline = Date(timeIntervalSinceNow: timeout)
    while !done() {
        guard Date() < deadline else { return false }
        usleep(2_000)
    }
    return true
}

/// Dispatches GLib default-context sources (where `runOnMain` lands) until `done` holds or `timeout` passes.
@MainActor
@discardableResult
func pumpGLib(timeout: TimeInterval, until done: () -> Bool = { false }) -> Bool {
    let deadline = Date(timeIntervalSinceNow: timeout)
    while !done() {
        guard Date() < deadline else { return false }
        if g_main_context_iteration(nil, 0) == 0 { usleep(1_000) }
    }
    return true
}

/// Splits a client's stream into lines, keeping bytes past a newline for the next call. One thread at a time.
final class ControlLineReader: @unchecked Sendable {
    let client: ControlTestClient
    private var buffer = Data()

    init(_ client: ControlTestClient) { self.client = client }

    /// The next line, or nil when the peer closes or `timeout` passes first.
    func next(timeout: TimeInterval) -> Data? {
        let deadline = Date(timeIntervalSinceNow: timeout)
        var chunk = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            if let newline = buffer.firstIndex(of: 0x0A) {
                let line = Data(buffer[buffer.startIndex..<newline])
                buffer.removeSubrange(buffer.startIndex...newline)
                return line
            }
            guard deadline.timeIntervalSinceNow > 0 else { return nil }
            var pfd = pollfd(fd: client.fd, events: Int16(POLLIN), revents: 0)
            guard poll(&pfd, 1, 50) > 0 else { continue }
            let count = recv(client.fd, &chunk, chunk.count, 0)
            if count < 0, errno == EINTR { continue }
            guard count > 0 else { return nil }
            buffer.append(contentsOf: chunk[0..<count])
        }
    }
}

/// Installs the GLib `MainTimer` seam for `body`, so timers fire when the test pumps GLib.
@MainActor
func withGLibMainTimer<R>(_ body: () throws -> R) rethrows -> R {
    let original = MainTimer.scheduleTimer
    defer { MainTimer.scheduleTimer = original }
    installGLibMainTimer()
    return try body()
}
