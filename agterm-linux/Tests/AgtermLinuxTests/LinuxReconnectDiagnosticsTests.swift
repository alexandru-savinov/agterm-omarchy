import Foundation
import Testing
@testable import AgtermLinux

@MainActor
struct LinuxReconnectDiagnosticsTests {
    @Test func titleMetadataNeverContainsRawTitlesOrNonces() throws {
        let secret = "fixture-private-nonce"
        let ordinary = LinuxReconnectDiagnostics.titleFields("private-host: private-command")
        let malformed = LinuxReconnectDiagnostics.titleFields("agterm-remote;private malformed")
        let link = LinuxReconnectDiagnostics.titleFields("agterm-remote;\(secret):lost")
        let text = String(decoding: try JSONSerialization.data(withJSONObject: [ordinary, malformed, link]), as: UTF8.self)
        #expect(!text.contains("private-host") && !text.contains("private-command"))
        #expect(!text.contains(secret) && !text.contains("private malformed"))
        #expect(ordinary["titleKind"] as? String == "ordinary")
        #expect(malformed["titleKind"] as? String == "reserved-other")
        #expect(link["nonceTag"] as? String == LinuxReconnectDiagnostics.nonceTag(secret))
        #expect(LinuxReconnectDiagnostics.nonceTag("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    @Test func sinkRequiresExplicitOptInAndPrivateDirectory() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(LinuxReconnectDiagnostics.configuredSink(["AGTERM_DIAG357_DIR": root.path]) == nil)
        #expect(LinuxReconnectDiagnostics.configuredSink(["AGTERM_DIAG357": "0", "AGTERM_DIAG357_DIR": root.path]) == nil)
        #expect(LinuxReconnectDiagnostics.configuredSink(["AGTERM_DIAG357": "1", "AGTERM_DIAG357_DIR": root.path]) != nil)
        for mode in [0o755, 0o1700] {
            try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: root.path)
            #expect(LinuxReconnectDiagnosticSink(root: root.path) == nil)
        }
    }

    @Test func sinkCreatesExclusivePrivateFilesAndRefusesReplacement() throws {
        let root = try directory()
        let moved = root.appendingPathExtension("moved")
        defer {
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: moved)
        }
        let sink = try #require(LinuxReconnectDiagnosticSink(root: root.path))
        let incarnation = UUID(), name = incarnation.uuidString + ".log"
        let identity = try #require(sink.createLog(incarnation: incarnation))
        let file = root.appendingPathComponent(name)
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        let directoryAttributes = try FileManager.default.attributesOfItem(atPath: root.path)
        let expected = try [directoryAttributes[.systemNumber], directoryAttributes[.systemFileNumber],
                            attributes[.systemNumber], attributes[.systemFileNumber]].map {
            String(try #require($0 as? NSNumber).uint64Value)
        }.joined(separator: ":")
        #expect(identity == expected)
        try "preserved".write(to: file, atomically: false, encoding: .utf8)
        #expect(sink.createLog(incarnation: incarnation) == nil)
        #expect(try String(contentsOf: file, encoding: .utf8) == "preserved")
        try FileManager.default.moveItem(at: root, to: moved)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        #expect(sink.createLog(incarnation: UUID()) == nil)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path).isEmpty)
    }

    @Test func sinkRefusesDirectoryAndFileSymlinks() throws {
        let root = try directory()
        let alias = root.appendingPathExtension("alias")
        defer {
            try? FileManager.default.removeItem(at: alias)
            try? FileManager.default.removeItem(at: root)
        }
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: root)
        #expect(LinuxReconnectDiagnosticSink(root: alias.path) == nil)
        let sink = try #require(LinuxReconnectDiagnosticSink(root: root.path))
        let target = root.appendingPathComponent("keep")
        try "preserved".write(to: target, atomically: false, encoding: .utf8)
        let incarnation = UUID()
        try FileManager.default.createSymbolicLink(at: root.appendingPathComponent(incarnation.uuidString + ".log"),
                                                   withDestinationURL: target)
        #expect(sink.createLog(incarnation: incarnation) == nil)
        #expect(try String(contentsOf: target, encoding: .utf8) == "preserved")
    }

    @Test func writerRequiresExplicitReadableModuleAndExecutableInterpreter() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let interpreter = root.appendingPathComponent("fixture-interpreter")
        let module = root.appendingPathComponent("fixture-module")
        try "unused fixture".write(to: interpreter, atomically: false, encoding: .utf8)
        try "unused fixture".write(to: module, atomically: false, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: interpreter.path)
        let values = ["AGTERM_DIAG357_INTERPRETER": interpreter.path, "AGTERM_DIAG357_WRITER": module.path]
        #expect(LinuxReconnectDiagnostics.configuredWriter([:]) == nil)
        #expect(LinuxReconnectDiagnostics.configuredWriter(values)?.module == module.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o400], ofItemAtPath: interpreter.path)
        #expect(LinuxReconnectDiagnostics.configuredWriter(values) == nil)
        #expect(LinuxReconnectDiagnostics.configuredWriter([
            "AGTERM_DIAG357_INTERPRETER": "/bin/sh", "AGTERM_DIAG357_WRITER": root.path]) == nil)
    }

    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("reconnect-sink-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        return root
    }
}
