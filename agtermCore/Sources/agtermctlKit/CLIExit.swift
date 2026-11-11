import ArgumentParser
import Foundation

extension Agtermctl {
    /// Transport failure is distinct from a decoded server refusal or a command's own exit status.
    public static func mainWithTransportStatus() {
        do {
            var command = try parseAsRoot()
            try command.run()
        } catch {
            if let failure = error as? SocketClientError, failure.kind == .transport {
                FileHandle.standardError.write(Data((fullMessage(for: error) + "\n").utf8))
                exit(withError: controlExitCode(for: error))
            }
            exit(withError: error)
        }
    }

    static func controlExitCode(for error: Error) -> ExitCode {
        if let failure = error as? SocketClientError, failure.kind == .transport { return ExitCode(4) }
        return exitCode(for: error)
    }
}
