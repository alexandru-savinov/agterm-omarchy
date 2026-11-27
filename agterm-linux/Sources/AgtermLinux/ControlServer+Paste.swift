import Foundation
import agtermCore

extension ControlServer {
    /// The worker waits for submission to the engine while GTK services the clipboard. The default
    /// deadline leaves room for the origin copy's SSH budget of up to 120 seconds for a 100 MiB bundle.
    func performPasteRequest(_ request: ControlRequest, deadline: DispatchTime = .now() + 150) -> ControlResponse {
        let box = LinuxRemoteCompanionBox(), ready = DispatchSemaphore(value: 0)
        runOnMain { MainActor.assumeIsolated {
            let finish: @MainActor (ControlResponse) -> Void = { response in
                PasteDebug.log("control reply ok=\(response.ok) error=\(response.error ?? "none")")
                box.response = response
                ready.signal()
            }
            switch Self.route(for: request) {
            case .failure(let reason): finish(ControlResponse(ok: false, error: reason))
            case .controller(nil): finish(ControlResponse(ok: false, error: "no controller"))
            case .controller(let controller?):
                let pane = request.args?.pane.flatMap(StatusPane.init(controlName:))
                if let raw = request.args?.pane, pane == nil {
                    return finish(controller.err("invalid pane: \(raw)"))
                }
                switch controller.pasteSurface(request.target, pane: pane) {
                case .failure(let response): finish(response)
                case .success(let surface):
                    surface.pasteClipboard(deadline: deadline) { reason in
                        finish(reason.map(controller.err) ?? controller.ok(surface.sessionID))
                    }
                }
            }
        } }
        guard ready.wait(timeout: deadline) == .success else {
            PasteDebug.log("control deadline fired")
            return ControlResponse(ok: false, error: LinuxPasteTransfer.timeoutReason)
        }
        return box.response
    }
}

@MainActor
extension AppController {
    enum PasteSurface {
        case success(GhosttySurface)
        case failure(ControlResponse)
    }
    func pasteSurface(_ target: String?, pane: StatusPane?) -> PasteSurface {
        switch resolveSessionResponse(target) {
        case .failure(let response): return .failure(response)
        case .success(let id):
            let surface: GhosttySurface? = switch pane {
            case nil: focusedSurface(for: id)
            case .left: surfaces[id]
            case .right: splitSurfaces[id]
            case .scratch: scratchSurfaces[id]
            }
            guard let surface, surface.isRealized else { return .failure(err("session not realized")) }
            return .success(surface)
        }
    }
}
