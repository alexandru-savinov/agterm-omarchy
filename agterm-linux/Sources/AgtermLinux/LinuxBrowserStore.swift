import CGtk
import CWebKit
import Foundation
import agtermCore

/// One persistent WebKit network session per isolated agterm state directory.
@MainActor
final class LinuxBrowserStore {
    static let shared = LinuxBrowserStore()
    private(set) var session: OpaquePointer?
    private var clearing = false

    func failure() -> String? {
        if clearing { return "browser storage is being cleared" }
        if session != nil { return nil }
        do {
            let root = linuxStateDirectory()
            let id = try BrowserProfile(directory: root).identifier()
            let data = root.appendingPathComponent("browser/\(id.uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
            session = data.path.withCString { path in
                data.appendingPathComponent("cache").path.withCString { cache in
                    webkit_network_session_new(path, cache)
                }
            }
            return session == nil ? "could not create browser storage" : nil
        } catch { return String(describing: error) }
    }

    func clear(completion: @escaping @MainActor (ControlResponse) -> Void) {
        if clearing { return completion(ControlResponse(ok: false, error: "browser.clear: browser storage is being cleared")) }
        let count = LinuxHtmlOverlayRegistry.shared.persistentPageCount
        guard count == 0 else {
            return completion(ControlResponse(ok: false, error: "browser.clear: \(count) persistent page(s) still open"))
        }
        do {
            guard try BrowserProfile(directory: linuxStateDirectory()).existingIdentifier() != nil else {
                return completion(ControlResponse(ok: true))
            }
        } catch { return completion(ControlResponse(ok: false, error: "browser.clear: " + String(describing: error))) }
        if let error = failure() { return completion(ControlResponse(ok: false, error: "browser.clear: " + error)) }
        guard let session, let manager = webkit_network_session_get_website_data_manager(session) else {
            return completion(ControlResponse(ok: false, error: "browser.clear: browser storage is unavailable"))
        }
        clearing = true
        let context = LinuxBrowserClearContext { [weak self] response in
            self?.clearing = false
            completion(response)
        }
        let start = {
            webkit_website_data_manager_clear(manager, WEBKIT_WEBSITE_DATA_ALL, 0, nil, onLinuxBrowserClear,
                                             Unmanaged.passRetained(context).toOpaque())
        }
        switch LinuxBrowserClearInjection.next() {
        case nil: start()
        case .hold: MainTimer.schedule(after: 1) { start() }
        case .fail:
            MainTimer.schedule(after: 1) {
                context.finish(ControlResponse(ok: false,
                                               error: "browser.clear: injected by \(LinuxBrowserClearInjection.environmentKey)"))
            }
        }
    }
}

/// Debug/AT-SPI-only: a comma list of `hold`/`fail`, one per started clear, so the smoke can hold the
/// clearing window open and fail a clear without WebKit's cooperation.
@MainActor
enum LinuxBrowserClearInjection {
    enum Step: String { case hold, fail }
    static let environmentKey = "AGTERM_ATSPI_BROWSER_CLEAR"
    private static var steps: [Step]?

    static func next(environment: [String: String] = ProcessInfo.processInfo.environment) -> Step? {
        #if DEBUG
        guard environment["AGTERM_ATSPI_SCENARIO"] != nil else { return nil }
        if steps == nil {
            steps = (environment[environmentKey] ?? "").split(separator: ",").compactMap { Step(rawValue: String($0)) }
        }
        return steps?.isEmpty == false ? steps?.removeFirst() : nil
        #else
        return nil
        #endif
    }
}

@MainActor
private final class LinuxBrowserClearContext {
    let finish: @MainActor (ControlResponse) -> Void
    init(_ finish: @escaping @MainActor (ControlResponse) -> Void) { self.finish = finish }
}

private let onLinuxBrowserClear: @MainActor @convention(c)
    (UnsafeMutablePointer<GObject>?, OpaquePointer?, gpointer?) -> Void = { (object: UnsafeMutablePointer<GObject>?, result: OpaquePointer?, data: gpointer?) in
        guard let data else { return }
        let context = Unmanaged<LinuxBrowserClearContext>.fromOpaque(data).takeRetainedValue()
        var error: UnsafeMutablePointer<GError>?
        let ok = webkit_website_data_manager_clear_finish(OpaquePointer(object), result, &error) != 0
        let message = error.map { String(cString: $0.pointee.message) }
        if let error { g_error_free(error) }
        context.finish(ControlResponse(ok: ok, error: ok ? nil : "browser.clear: " + (message ?? "could not clear browser storage")))
    }
