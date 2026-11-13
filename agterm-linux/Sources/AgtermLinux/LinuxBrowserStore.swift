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
        let injection = LinuxBrowserClearInjection.next()
        let context = LinuxBrowserClearContext(mode: injection?.rawValue ?? "clear") { [weak self] response in
            self?.clearing = false
            completion(response)
        }
        let start: @MainActor () -> Void = {
            webkit_website_data_manager_clear(manager, WEBKIT_WEBSITE_DATA_ALL, 0, nil, onLinuxBrowserClear,
                                             Unmanaged.passRetained(context).toOpaque())
        }
        switch injection {
        case nil: start()
        case .hold: LinuxBrowserClearInjection.hold(start, failure: context.finish)
        case .fail:
            MainTimer.schedule(after: 1) {
                context.finish(ControlResponse(ok: false,
                                               error: "browser.clear: injected by \(LinuxBrowserClearInjection.environmentKey)"))
            }
        case .cancel:
            // Cancellation reaches the real WebKit callback/finish path, including GError ownership.
            let cancellable = g_cancellable_new()
            g_cancellable_cancel(cancellable)
            webkit_website_data_manager_clear(manager, WEBKIT_WEBSITE_DATA_ALL, 0, cancellable, onLinuxBrowserClear,
                                             Unmanaged.passRetained(context).toOpaque())
            g_object_unref(cancellable.map { UnsafeMutableRawPointer($0) })
        }
    }
}

/// DEBUG/AT-SPI-only controls; normal and Release clears always take the unmodified WebKit path.
@MainActor
private enum LinuxBrowserClearInjection {
    enum Step: String { case hold, fail, cancel }
    static let environmentKey = "AGTERM_ATSPI_BROWSER_CLEAR"
    private static var steps: [Step]?

    static var traceEnabled: Bool {
        #if DEBUG
        let environment = ProcessInfo.processInfo.environment
        return environment["AGTERM_ATSPI_SCENARIO"] != nil && environment[environmentKey] != nil
        #else
        return false
        #endif
    }

    static func next() -> Step? {
        guard traceEnabled else { return nil }
        if steps == nil {
            steps = (ProcessInfo.processInfo.environment[environmentKey] ?? "")
                .split(separator: ",").compactMap { Step(rawValue: String($0)) }
        }
        return steps?.isEmpty == false ? steps?.removeFirst() : nil
    }

    static func hold(_ start: @escaping @MainActor () -> Void,
                     failure: @escaping @MainActor (ControlResponse) -> Void) {
        guard let release = ProcessInfo.processInfo.environment["AGTERM_ATSPI_BROWSER_CLEAR_RELEASE"], !release.isEmpty else {
            return failure(ControlResponse(ok: false, error: "browser.clear: test hold release path missing"))
        }
        let deadline = ProcessInfo.processInfo.systemUptime + 10
        @MainActor func pollRelease() {
            if FileManager.default.fileExists(atPath: release) { return start() }
            if ProcessInfo.processInfo.systemUptime >= deadline {
                return failure(ControlResponse(ok: false, error: "browser.clear: test hold release timed out"))
            }
            MainTimer.schedule(after: 0.02) { pollRelease() }
        }
        MainTimer.schedule(after: 0) { pollRelease() }
    }
}

@MainActor
private final class LinuxBrowserClearContext {
    private let completion: @MainActor (ControlResponse) -> Void
    #if DEBUG
    private let traceID: String?
    private let mode: String
    private var callbacks = 0
    private var completions = 0
    #endif

    init(mode: String, _ completion: @escaping @MainActor (ControlResponse) -> Void) {
        self.completion = completion
        #if DEBUG
        self.mode = mode
        traceID = LinuxBrowserClearInjection.traceEnabled ? UUID().uuidString : nil
        trace("start")
        #endif
    }

    func finish(_ response: ControlResponse) {
        #if DEBUG
        completions += 1
        trace("finish \(completions)")
        #endif
        completion(response)
    }

    #if DEBUG
    func didCallback(ok: Bool, cancelled: Bool) {
        callbacks += 1
        let result = cancelled ? "cancelled" : ok ? "ok" : "error"
        trace("callback \(callbacks) \(result)")
    }

    private func trace(_ event: String) {
        guard let traceID else { return }
        let line = "AGTERM_BROWSER_CLEAR \(ProcessInfo.processInfo.processIdentifier) \(traceID) \(mode) \(event)\n"
        FileHandle.standardError.write(Data(line.utf8))
    }
    #endif
}

private let onLinuxBrowserClear: @MainActor @convention(c)
    (UnsafeMutablePointer<GObject>?, OpaquePointer?, gpointer?) -> Void = { (object: UnsafeMutablePointer<GObject>?, result: OpaquePointer?, data: gpointer?) in
        guard let data else { return }
        let context = Unmanaged<LinuxBrowserClearContext>.fromOpaque(data).takeRetainedValue()
        var error: UnsafeMutablePointer<GError>?
        let ok = webkit_website_data_manager_clear_finish(OpaquePointer(object), result, &error) != 0
        let message = error.map { String(cString: $0.pointee.message) }
        #if DEBUG
        let cancelled = error.map { g_error_matches($0, g_io_error_quark(), Int32(G_IO_ERROR_CANCELLED.rawValue)) != 0 } ?? false
        context.didCallback(ok: ok, cancelled: cancelled)
        #endif
        if let error { g_error_free(error) }
        context.finish(ControlResponse(ok: ok, error: ok ? nil : "browser.clear: " + (message ?? "could not clear browser storage")))
    }
