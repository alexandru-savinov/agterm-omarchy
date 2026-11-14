import Foundation
import agtermCore

/// Local zmx control requests (`zmx.list`, `zmx.prune`, `zmx.kill`, `zmx.screen`, host-less `zmx.tree`),
/// staged so every zmx process runs on the control worker, never on GTK's main loop. Each zmx call can wait
/// up to the client timeout plus the kill grace, and `zmx.prune` makes several of them.
extension ControlServer {
    static func localZmxStage(_ request: ControlRequest) -> ControlStage? {
        switch request.cmd {
        case .zmxList, .zmxPrune, .zmxKill, .zmxScreen: break
        case .zmxTree where request.args?.host?.linuxTrimmedOrNil == nil: break
        default: return nil
        }
        return .main { ControlServer.beginLocalZmx(request) }
    }

    /// The first main hop only checks the request and reads the client. Model access waits for a later hop,
    /// where the controller is resolved again, since a window can close while zmx runs.
    @MainActor private static func beginLocalZmx(_ request: ControlRequest) -> ControlStage {
        if case .failure(let error) = ControlServer.route(for: request) { return .done(ControlResponse(ok: false, error: error)) }
        if let refusal = localZmxRefusal(request) { return .done(refusal) }
        guard let client = gZmxClient else { return .done(ControlResponse(ok: false, error: LocalZmxError.unavailable)) }
        if request.cmd == .zmxScreen, let name = request.args?.name?.trimmingCharacters(in: .whitespacesAndNewlines) {
            let all = request.args?.all == true || request.args?.lines != nil
            let lines = request.args?.lines
            return .worker {
                guard let screen = client.screen(name: name, all: all) else {
                    return .done(ControlResponse(ok: false, error: "could not read the zmx screen of \(name)"))
                }
                return .done(ControlResponse(ok: true, result: ControlResult(text: lines.map(screen.lastLines) ?? screen.text)))
            }
        }
        return .worker {
            guard let observed = client.listSessions() else {
                return .done(ControlResponse(ok: false, error: LocalZmxError.listFailed))
            }
            return .main {
                switch ControlServer.route(for: request) {
                case .controller(let controller?): return controller.continueLocalZmx(request, observed: observed, client: client)
                case .controller(nil): return .done(ControlResponse(ok: false, error: "no controller"))
                case .failure(let error): return .done(ControlResponse(ok: false, error: error))
                }
            }
        }
    }

    /// Argument checks, in the order the shared dispatcher ran them before any zmx call.
    static func localZmxRefusal(_ request: ControlRequest) -> ControlResponse? {
        switch request.cmd {
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
            return nil
        case .zmxKill:
            guard let target = request.target, !target.isEmpty else {
                return ControlResponse(ok: false, error: "zmx.kill requires an explicit --target")
            }
            guard let rawPane = request.args?.pane, ZmxPaneRole(controlName: rawPane) != nil else {
                return ControlResponse(ok: false, error: "zmx.kill requires --pane left|right")
            }
            guard request.args?.force == true else {
                return ControlResponse(ok: false, error: "zmx.kill requires --force")
            }
            return nil
        default:
            return nil
        }
    }
}

enum LocalZmxError {
    static let unavailable = "zmx is unavailable in this instance"
    static let listFailed = "could not read the zmx session list"
}
