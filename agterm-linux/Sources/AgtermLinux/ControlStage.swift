import Foundation
import agtermCore

/// One step of a control request whose slow part must stay off the GTK main thread.
///
/// A zmx process wait runs as `.worker` on the request's control-socket worker, the same place
/// `ControlServer.dispatchRemoteZmx` waits on ssh. `.main` hops back to GTK only to read or change app and
/// model state; each hop is one short turn that never waits on a process. `ControlServer.perform` drives
/// the steps in order and writes the single reply once the chain reaches `.done`.
enum ControlStage: Sendable {
    case done(ControlResponse)
    case worker(@Sendable () -> ControlStage)
    case main(@MainActor @Sendable () -> ControlStage)

    /// Runs every step on the calling main-thread turn. Only for a caller with no control worker to hand
    /// the work to; the control server never takes this path.
    @MainActor func runInline() -> ControlResponse {
        var stage = self
        while true {
            switch stage {
            case .done(let response): return response
            case .worker(let step): stage = step()
            case .main(let step): stage = step()
            }
        }
    }
}

/// Lets a main-thread control handler deep in the dispatcher hand its blocking tail to the control worker
/// without threading a stage type through every dispatcher signature.
///
/// `ControlServer` collects around one synchronous dispatch turn. A handler that reaches a zmx call returns
/// `handOff(stage)` at once; the server then discards that placeholder response and runs `stage` on the
/// worker. A handler reached outside a collecting turn runs the stage inline, as before.
@MainActor
enum ControlStageHandoff {
    private static var collecting = false
    private static var handed: ControlStage?

    /// Never reaches a client: the collecting server replaces it with the handed stage's reply.
    static let placeholder = ControlResponse(ok: false, error: "internal: a handed-off control stage was not run")

    /// Runs `dispatch` and returns the stage it handed off, or its plain response when it handed none.
    static func collect(_ dispatch: () -> ControlResponse) -> ControlStage {
        let outer = (collecting, handed)
        collecting = true
        handed = nil
        defer { (collecting, handed) = outer }
        let response = dispatch()
        return handed ?? .done(response)
    }

    /// The handler's return value. One hand-off per turn; a second one in the same turn runs inline.
    static func handOff(_ stage: ControlStage) -> ControlResponse {
        guard collecting, handed == nil else { return stage.runInline() }
        handed = stage
        return placeholder
    }
}
