import Foundation
import agtermCore

@MainActor
extension LinuxControlDispatcher {
    func dispatchNewParityCommand(_ request: ControlRequest) -> ControlResponse? {
        switch request.cmd {
        case .sessionOverlaySearch, .sessionOverlayChanges: return dispatchTextPage(request)
        case .sessionOverlayOpen, .sessionOverlayClose, .sessionOverlayResize, .sessionOverlayResult,
             .sessionOverlayReload, .sessionOverlayNavigate, .sessionOverlaySubmit, .sessionOverlayCopy,
             .sessionOverlayText, .sessionOverlayHide, .sessionOverlayShow, .sessionOverlayToggle:
            return dispatchV039OverlayCommand(request)
        case .sessionTint, .workspaceTint:
            return dispatchTint(request)
        case .sessionCompanionNew, .sessionCompanionPage, .sessionCompanionDuplicate, .sessionCompanionPageAttach,
             .sessionCollapse, .sessionExpand:
            return dispatchCompanionCommand(request)
        case .recentList, .recentOpen, .recentClear:
            if request.cmd == .recentOpen {
                return actions.openRecentClosed(request.target, window: request.args?.window)
            }
            if request.target != nil || request.args?.window != nil {
                return ControlResponse(ok: false, error: "\(request.cmd.rawValue) takes no target or --window")
            }
            return request.cmd == .recentList ? actions.listRecentClosed() : actions.clearRecentClosed()
        case .workspaceFlag:
            guard let mode = ControlToggleMode.parse(request.args?.mode) else {
                return ControlResponse(ok: false, error: "invalid flag mode: \(request.args?.mode ?? "toggle")")
            }
            return actions.setWorkspaceFlag(request.target, window: request.args?.window, mode: mode)
        case .browserLinks:
            if request.target != nil || request.args?.window != nil {
                return ControlResponse(ok: false, error: "browser.links takes no target or --window")
            }
            if let raw = request.args?.mode {
                guard let mode = LinkOpenMode(rawValue: raw) else {
                    return ControlResponse(ok: false, error: "invalid link mode: \(raw)")
                }
                return actions.linkOpenMode(mode)
            }
            return actions.linkOpenMode(nil)
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
        case .zmxScreen:
            guard let name = request.args?.name?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty,
                  !name.unicodeScalars.contains(where: { $0.properties.isWhitespace || $0.value < 0x20 || $0.value == 0x7f }) else {
                return ControlResponse(ok: false, error: "zmx.screen requires a daemon name")
            }
            if request.args?.all == true, request.args?.lines != nil {
                return ControlResponse(ok: false, error: "use either --all or --lines, not both")
            }
            if let lines = request.args?.lines, lines <= 0 {
                return ControlResponse(ok: false, error: "--lines must be greater than 0")
            }
            return actions.readZmxScreen(name: name, fullBuffer: request.args?.all == true,
                                         lines: request.args?.lines)
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
