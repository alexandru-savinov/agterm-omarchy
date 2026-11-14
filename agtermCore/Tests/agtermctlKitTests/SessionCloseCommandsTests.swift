import ArgumentParser
import Testing
import agtermCore
@testable import agtermctlKit

struct SessionCloseCommandsTests {
    private func request(_ argv: [String]) throws -> ControlRequest {
        let parsed = try Agtermctl.parseAsRoot(argv)
        guard let command = parsed as? any RequestCommand else {
            throw SocketClientError("parsed \(argv) is not a RequestCommand")
        }
        return try command.makeRequest()
    }

    @Test func undoableSingleTarget() throws {
        #expect(try request(["session", "close", "--target", "x", "--undoable"])
            == ControlRequest(cmd: .sessionClose, target: "x", args: ControlArgs(undoable: true)))
    }

    @Test func undoableDefaultsToTheActiveSession() throws {
        #expect(try request(["session", "close", "--undoable"])
            == ControlRequest(cmd: .sessionClose, target: "active", args: ControlArgs(undoable: true)))
    }

    @Test func undoableBatchKeepsTheTargetsAndWindow() throws {
        let expected = ControlRequest(cmd: .sessionClose, target: "a",
                                      args: ControlArgs(targets: ["a", "b"], window: "w1", undoable: true))
        #expect(try request(["session", "close", "--target", "a", "--target", "b", "--undoable", "--window", "w1"])
            == expected)
    }
}
