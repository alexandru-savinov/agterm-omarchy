import Foundation

/// DIAGNOSTIC ONLY (#357, never merged): one stderr line per reconnect transition.
public enum Trace357 {
    static let tag: String = {
        let id = ProcessInfo.processInfo.environment["AGTERM_APP_ID"] ?? ""
        return id.hasSuffix(".viewer") ? "viewer" : id.hasSuffix(".origin") ? "origin" : "app"
    }()

    public static func log(_ message: @autoclosure () -> String) {
        let line = "agterm-trace357 \(ProcessInfo.processInfo.systemUptime) \(tag) pid=\(ProcessInfo.processInfo.processIdentifier) "
            + message() + "\n"
        FileHandle.standardError.write(Data(line.utf8))
    }

    public static func short(_ id: UUID?) -> String { id.map { String($0.uuidString.prefix(8)) } ?? "nil" }
    public static func short(_ id: String?) -> String { id.map { String($0.prefix(8)) } ?? "nil" }
}
