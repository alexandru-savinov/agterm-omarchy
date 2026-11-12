import XCTest

@MainActor
final class SeededSettingsLaunchUITests: ControlAPITestCase {
    override var seededSettings: [String: Any]? {
        ["fontSize": 27, "backgroundOpacity": 0.6, "backgroundBlur": 20, "theme": "agterm"]
    }

    // libghostty loaded its config before the generated ghostty-settings.conf was written on a fresh state dir
    func testFirstLaunchTerminalUsesSeededSettings() throws {
        let id = try activeSessionID()
        var size: Double?
        XCTAssertTrue(poll(until: { size = self.liveFontSize(session: id); return size != nil }(), timeout: 15),
                      "the seeded session should report a live font size")
        XCTAssertEqual(size, 27, "the first terminal should start with the seeded font size")
    }

    private func liveFontSize(session id: String) -> Double? {
        guard let tree = try? sendCommand(#"{"cmd":"tree"}"#),
              let result = tree["result"] as? [String: Any],
              let t = result["tree"] as? [String: Any],
              let workspaces = t["workspaces"] as? [[String: Any]] else { return nil }
        let sessions = workspaces.flatMap { $0["sessions"] as? [[String: Any]] ?? [] }
        let node = sessions.first { ($0["id"] as? String)?.lowercased() == id.lowercased() }
        return node?["fontSize"] as? Double
    }
}
