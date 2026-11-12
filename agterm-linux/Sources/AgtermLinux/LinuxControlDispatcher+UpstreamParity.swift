import agtermCore
import Foundation

@MainActor
extension LinuxControlDispatcher {
    func dispatchZmxCommand(_ request: ControlRequest) -> ControlResponse? {
        switch request.cmd {
        case .restoreMode:
            guard let raw = request.args?.mode else { return actions.readRestoreMode() }
            guard let mode = RestoreMode(rawValue: raw) else {
                return ControlResponse(ok: false, error: "invalid restore mode: \(raw)")
            }
            return actions.setRestoreMode(mode)
        case .zmxList, .zmxPrune, .zmxKill, .zmxTree, .zmxAttach:
            // ControlServer serves these before any main-thread dispatch, so their zmx and ssh waits stay
            // on the control worker (ControlServer+LocalZmx.swift, ControlServer.dispatchRemoteZmx).
            return nil
        default:
            preconditionFailure("unexpected zmx command: \(request.cmd.rawValue)")
        }
    }

    func dispatchSessionContext(_ request: ControlRequest) -> ControlResponse {
        let args = request.args
        let context: String?
        switch args?.mode ?? "" {
        case "set":
            guard let text = args?.text else {
                return ControlResponse(ok: false, error: "session.context set requires text")
            }
            switch Session.validateContext(text) {
            case .valid(let value): context = value
            case .invalid(let message): return ControlResponse(ok: false, error: message)
            }
        case "clear":
            guard args?.text == nil else {
                return ControlResponse(ok: false, error: "session.context clear takes no text")
            }
            context = nil
        default:
            let rawMode = args?.mode ?? ""
            return ControlResponse(ok: false,
                                   error: "invalid context mode: \(rawMode) (set|clear)")
        }
        return actions.setSessionContext(request.target, window: args?.window, context: context)
    }
}
