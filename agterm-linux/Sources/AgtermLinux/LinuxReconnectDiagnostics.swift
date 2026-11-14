import CGtk
import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// Opt in with AGTERM_DIAG357=1 and AGTERM_DIAG357_DIR naming an existing owned 0700 directory.
/// Wrapper records stay outside the PTY; app stderr receives at most 4096 native events plus a limit marker.
/// This adds no control action or model state to drive. The operator retains and removes the private files.
@MainActor
enum LinuxReconnectDiagnostics {
    private static let sink = configuredSink(ProcessInfo.processInfo.environment)
    private static let writer = configuredWriter(ProcessInfo.processInfo.environment)
    private static var observingBook = false
    private static var count = 0
    static var enabled: Bool { sink != nil }

    static func configuredSink(_ environment: [String: String]) -> LinuxReconnectDiagnosticSink? {
        guard environment["AGTERM_DIAG357"] == "1", let root = environment["AGTERM_DIAG357_DIR"] else { return nil }
        return LinuxReconnectDiagnosticSink(root: root)
    }

    static func incarnation() -> UUID? { enabled ? UUID() : nil }

    /// The operator admits the exact interpreter/module/loader before enabling observation. Never use PATH.
    static func configuredWriter(_ environment: [String: String]) -> (interpreter: String, module: String)? {
        guard let interpreter = environment["AGTERM_DIAG357_INTERPRETER"],
              let module = environment["AGTERM_DIAG357_WRITER"],
              [interpreter, module].allSatisfy({ $0.hasPrefix("/") && !$0.utf8.contains(0) }),
              [interpreter, module].allSatisfy({
                  (try? FileManager.default.attributesOfItem(atPath: $0)[.type] as? FileAttributeType) == .typeRegular
              }),
              FileManager.default.isExecutableFile(atPath: interpreter),
              FileManager.default.isReadableFile(atPath: module) else { return nil }
        return (interpreter, module)
    }

    static func childEnvironment(_ base: [String: String], incarnation: UUID?) -> [String: String] {
        guard let sink else { return base }
        installBookObserver()
        var result = base
        for key in ["AGTERM_DIAG357_INCARNATION", "AGTERM_DIAG357_NONCE_TAG", "AGTERM_DIAG357_DIR",
                    "AGTERM_DIAG357_IDENTITY", "AGTERM_DIAG357_INTERPRETER", "AGTERM_DIAG357_WRITER"] { result[key] = nil }
        result["AGTERM_DIAG357"] = "0"
        let pane = base["AGTERM_PANE_ID"].flatMap(UUID.init(uuidString:))
        let nonce = base[ZmxLeadAttachment.nonceVariable]
            ?? pane.flatMap { ZmxLeadBook.shared.states[$0]?.attachment.nonce }
        guard let incarnation, let pane, let nonce, let writer else { return result }
        let tag = nonceTag(nonce)
        let identity = tag == "none" ? nil : sink.createLog(incarnation: incarnation)
        emit("surface-environment", pane: pane, incarnation: incarnation,
             fields: ["nonceTag": tag, "wrapperSink": identity != nil])
        guard let identity else { return result }
        result["AGTERM_DIAG357"] = "1"
        result["AGTERM_DIAG357_DIR"] = sink.root
        result["AGTERM_DIAG357_INCARNATION"] = incarnation.uuidString
        result["AGTERM_DIAG357_NONCE_TAG"] = tag
        result["AGTERM_DIAG357_IDENTITY"] = identity
        result["AGTERM_DIAG357_INTERPRETER"] = writer.interpreter
        result["AGTERM_DIAG357_WRITER"] = writer.module
        return result
    }

    static func nonceTag(_ nonce: String?) -> String {
        guard let nonce, !nonce.isEmpty,
              let pointer = nonce.withCString({ g_compute_checksum_for_string(G_CHECKSUM_SHA256, $0, nonce.utf8.count) }) else { return "none" }
        defer { g_free(pointer) }
        return String(cString: pointer)
    }

    static func titleFields(_ title: String?) -> [String: Any] {
        guard let title else { return ["titlePresent": false, "titleKind": "absent"] }
        if let notice = RemoteLinkNotice(title: title) {
            return ["titlePresent": true, "titleKind": "remote-link", "nonceTag": nonceTag(notice.nonce)]
        }
        if let notice = ZmxLeadNotice(title: title) {
            return ["titlePresent": true, "titleKind": "lead", "nonceTag": nonceTag(notice.nonce),
                    "leadRole": notice.role.rawValue, "generation": notice.generation]
        }
        let reserved = title.hasPrefix("agterm-remote;") || title.hasPrefix(ZmxLeadNotice.prefix)
        return ["titlePresent": true, "titleKind": reserved ? "reserved-other" : "ordinary"]
    }

    static func title(_ pointer: UnsafePointer<CChar>?, surface: GhosttySurface?) {
        guard enabled else { return }
        record("native-title", surface: surface, fields: titleFields(pointer.map { String(cString: $0) }))
    }

    static func router(_ notice: RemoteLinkNotice, surface: any TerminalSurface, session: Session?,
                       pane: UUID?, held: ZmxLeadState?) {
        guard enabled else { return }
        let live = pane.flatMap { ZmxLeadBook.shared.states[$0] }
        let expected = live ?? held
        record("router-attempt", surface: surface as? GhosttySurface,
               fields: ["slotCurrent": pane != nil, "sessionPresent": session != nil,
                        "hostPresent": session?.remoteHost != nil, "liveAttachment": live != nil,
                        "heldAttachment": held != nil, "nonceMatches": notice.nonce == expected?.attachment.nonce,
                        "expectedNonceTag": nonceTag(expected?.attachment.nonce), "nonceTag": nonceTag(notice.nonce)])
    }

    static func record(_ stage: String, surface: GhosttySurface?, fields: [String: Any] = [:]) {
        guard enabled else { return }
        var fields = fields
        fields["surfacePresent"] = surface != nil
        fields["controllerPresent"] = surface?.controller != nil
        fields["waitAfterCommand"] = surface?.waitAfterCommand ?? false
        emit(stage, pane: surface.flatMap { UUID(uuidString: $0.paneToken) },
             incarnation: surface?.reconnectDiagnosticID, fields: fields)
    }

    private static func installBookObserver() {
        guard !observingBook else { return }
        observingBook = true
        RemoteReconnectBook.shared.onDiagnostic = { event in
            var fields: [String: Any] = ["waiting": event.waiting, "reasonPresent": event.reasonPresent]
            if let failures = event.failures { fields["failures"] = failures }
            if let probing = event.probing { fields["probing"] = probing }
            emit("book-" + event.stage.rawValue, pane: event.pane, incarnation: nil, fields: fields)
        }
    }

    private static func emit(_ stage: String, pane: UUID?, incarnation: UUID?, fields: [String: Any]) {
        guard enabled, count <= 4096 else { return }
        var row = count == 4096 ? [:] : fields
        row["event"] = count == 4096 ? "record-limit" : stage
        row["sequence"] = count
        row["pid"] = ProcessInfo.processInfo.processIdentifier
        row["uptime"] = ProcessInfo.processInfo.systemUptime
        row["pane"] = pane?.uuidString ?? "none"
        row["incarnation"] = incarnation?.uuidString ?? "none"
        count += 1
        guard let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) else { return }
        FileHandle.standardError.write(Data("DIAG357_NATIVE ".utf8) + data + Data("\n".utf8))
    }
}

/// Exclusive files beneath an already-private directory; never follow or overwrite an existing entry.
final class LinuxReconnectDiagnosticSink {
    let root: String
    private let descriptor: Int32
    private let identity: stat

    init?(root: String) {
        guard root.hasPrefix("/"), !root.utf8.contains(0) else { return nil }
        let fd = root.withCString { open($0, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW) }
        guard fd >= 0 else { return nil }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == geteuid(),
              info.st_mode & mode_t(CGtk.S_IFMT) == mode_t(CGtk.S_IFDIR),
              info.st_mode & 0o7777 == 0o700 else {
            _ = close(fd)
            return nil
        }
        self.root = root
        descriptor = fd
        identity = info
    }

    deinit { _ = close(descriptor) }

    func createLog(incarnation: UUID) -> String? {
        guard ownsRoot else { return nil }
        let name = incarnation.uuidString + ".log"
        let fd = name.withCString {
            openat(descriptor, $0, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, mode_t(0o600))
        }
        guard fd >= 0 else { return nil }
        var info = stat()
        let valid = fchmod(fd, mode_t(0o600)) == 0 && fstat(fd, &info) == 0
            && info.st_uid == geteuid() && info.st_mode & mode_t(CGtk.S_IFMT) == mode_t(CGtk.S_IFREG)
            && info.st_mode & 0o7777 == 0o600 && info.st_nlink == 1 && info.st_size == 0
        let closed = close(fd) == 0
        guard valid, closed, ownsRoot else { return nil }
        return "\(identity.st_dev):\(identity.st_ino):\(info.st_dev):\(info.st_ino)"
    }

    private var ownsRoot: Bool {
        var current = stat()
        return root.withCString({ lstat($0, &current) }) == 0
            && current.st_dev == identity.st_dev && current.st_ino == identity.st_ino
            && current.st_uid == geteuid() && current.st_mode & mode_t(CGtk.S_IFMT) == mode_t(CGtk.S_IFDIR)
            && current.st_mode & 0o7777 == 0o700
    }
}
