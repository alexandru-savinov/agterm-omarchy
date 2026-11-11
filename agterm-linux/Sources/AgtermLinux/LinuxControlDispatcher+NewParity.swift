import Foundation
import agtermCore

@MainActor
extension LinuxControlDispatcher {
    func dispatchNewParityCommand(_ request: ControlRequest) -> ControlResponse? {
        switch request.cmd {
        case .keymapRun:
            guard let name = request.args?.name, !name.isEmpty else {
                return ControlResponse(ok: false, error: "keymap.run requires a command name")
            }
            return actions.runCustomCommand(name: name, target: request.target, window: request.args?.window)
        case .sessionOverlaySubmit:
            guard let value = request.args?.value else {
                return ControlResponse(ok: false, error: OverlayHtmlError.submitValue)
            }
            switch parseOverlayPane(request.args?.pane) {
            case .rejected(let response): return response
            case .pane(let pane):
                return actions.submitSessionOverlay(request.target, window: request.args?.window, pane: pane, value: value)
            }
        case .sessionOverlayResult where request.args?.page != nil:
            guard let id = request.args?.page.flatMap(UUID.init(uuidString:)) else {
                return ControlResponse(ok: false, error: OverlayHtmlError.invalidPageID)
            }
            return actions.htmlPageResult(id)
        default: return nil
        }
    }
}

@MainActor
extension AppController {
    func submitSessionOverlay(_ target: String?, window: String?, pane: OverlayPane?, value: String) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            if let failure = store.submitHtmlOverlay(id, pane: pane, value: value) { return err(failure.message) }
            reconcile()
            return ok(id)
        }
    }

    func readSurfaceCursor(_ target: String?, window: String?, paneID: String?) -> ControlResponse {
        guard let paneID, !paneID.isEmpty else { return readSurfaceCursor(target, window: window) }
        let target = target?.trimmingCharacters(in: .whitespacesAndNewlines)
        if target == "quick" || target.flatMap(TerminalSurfaceID.init(rawValue:)) != nil {
            return err("surface.cursor: --pane-id takes a session target")
        }
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            guard let pane = store.session(withID: id)?.paneRole(forToken: paneID) else {
                return err("unknown pane id: \(paneID)")
            }
            let kind: TerminalZoomSurface = pane == .right ? .split : (pane == .scratch ? .scratch : .primary)
            return readSurfaceCursor(TerminalSurfaceID(sessionID: id, surface: kind).rawValue, window: window)
        }
    }

    func runCustomCommand(name: String, target: String?, window: String?) -> ControlResponse {
        guard let command = keymap.commands.first(where: { $0.name == name }) else {
            return err("no custom command named \(name)")
        }
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            guard runCustomCommand(command, targetSession: store.session(withID: id)) else {
                return err("command failed to launch: \(name)")
            }
            return ok(id)
        }
    }
}
