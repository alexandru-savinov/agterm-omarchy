import CGtk
import Foundation

enum PasteDebug {
    static func log(_ message: String) {
        let timestamp = Date().timeIntervalSince1970
        let line = "PASTE_DEBUG \(timestamp) pid=\(ProcessInfo.processInfo.processIdentifier) \(message)\n"
        FileHandle.standardError.write(Data(line.utf8))
    }

    static func formats(_ formats: OpaquePointer) -> String {
        guard let result = gdk_content_formats_to_string(formats) else { return "<nil>" }
        defer { g_free(result) }
        return String(cString: result)
    }

    static func result<T>(_ value: T?, error: UnsafeMutablePointer<GError>?) -> String {
        guard let error else { return value == nil ? "nil" : "ok" }
        defer { g_error_free(error) }
        return "error \(error.pointee.domain):\(error.pointee.code) \(String(cString: error.pointee.message))"
    }
}
