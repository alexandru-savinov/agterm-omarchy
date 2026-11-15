import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Linux browser profiles are isolated per state directory")
struct LinuxBrowserProfileIsolationTests {
    private static func stateRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("agterm-browser-isolation-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root.standardizedFileURL
    }

    private static func isInside(_ path: URL, _ root: URL) -> Bool {
        path.standardizedFileURL.path.hasPrefix(root.standardizedFileURL.path + "/")
    }

    @Test("the persistent data directory derives from its own state root and is reused")
    func dataDirectoryDerivesFromItsRoot() throws {
        let root = try Self.stateRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let data = try LinuxBrowserStore.dataDirectory(stateRoot: root)

        let id = try #require(try BrowserProfile(directory: root).existingIdentifier())
        #expect(data.standardizedFileURL.path == root.appendingPathComponent("browser/\(id.uuidString)").standardizedFileURL.path)
        #expect(try LinuxBrowserStore.dataDirectory(stateRoot: root) == data)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["browser-profile"])
    }

    @Test("two state roots never resolve to the same or a nested profile path")
    func twoRootsGetSeparatePaths() throws {
        let one = try Self.stateRoot()
        let two = try Self.stateRoot()
        defer {
            try? FileManager.default.removeItem(at: one)
            try? FileManager.default.removeItem(at: two)
        }

        let first = try LinuxBrowserStore.dataDirectory(stateRoot: one)
        let second = try LinuxBrowserStore.dataDirectory(stateRoot: two)

        #expect(first != second)
        #expect(first.lastPathComponent != second.lastPathComponent)
        #expect(Self.isInside(first, one) && !Self.isInside(first, two))
        #expect(Self.isInside(second, two) && !Self.isInside(second, one))
    }

    @Test("an ephemeral page creates no profile, directory, or session")
    func ephemeralNeverTouchesTheStateRoot() throws {
        let root = try Self.stateRoot()
        let missing = root.appendingPathComponent("never-created", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = LinuxBrowserStore()

        #expect(store.failure(persistent: false, stateRoot: root) == nil)
        #expect(store.failure(persistent: false, stateRoot: missing) == nil)

        #expect(store.session == nil)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path).isEmpty)
        #expect(!FileManager.default.fileExists(atPath: missing.path))
    }

    @Test("a disposable root's profile path stays out of the default state directory")
    func disposableRootStaysOffTheRealProfile() throws {
        let root = try Self.stateRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(!Self.isInside(root, PersistenceStore.defaultDirectory))

        let data = try LinuxBrowserStore.dataDirectory(stateRoot: root)

        #expect(Self.isInside(data, root))
        #expect(!Self.isInside(data, PersistenceStore.defaultDirectory))
    }

    @Test("AGTERM_STATE_DIR selects the root the profile derives from; an empty value falls back to the default")
    func stateDirectoryHonoursTheEnvironment() throws {
        let one = try Self.stateRoot()
        let two = try Self.stateRoot()
        defer {
            try? FileManager.default.removeItem(at: one)
            try? FileManager.default.removeItem(at: two)
        }

        let first = linuxStateDirectory(environment: ["AGTERM_STATE_DIR": one.path])
        let second = linuxStateDirectory(environment: ["AGTERM_STATE_DIR": two.path])

        #expect(first.standardizedFileURL.path == one.path)
        #expect(Self.isInside(try LinuxBrowserStore.dataDirectory(stateRoot: first), one))
        #expect(Self.isInside(try LinuxBrowserStore.dataDirectory(stateRoot: second), two))
        #expect(linuxStateDirectory(environment: ["AGTERM_STATE_DIR": ""]) == PersistenceStore.defaultDirectory)
        #expect(linuxStateDirectory(environment: [:]) == PersistenceStore.defaultDirectory)
    }

    @Test("a persistent page whose data directory cannot be created reports it and builds no session")
    func dataDirectoryCreationFailureIsReported() throws {
        let root = try Self.stateRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let blocker = root.appendingPathComponent("browser")
        try Data("not a directory".utf8).write(to: blocker)
        let store = LinuxBrowserStore()

        let error = store.failure(persistent: true, stateRoot: root)

        #expect(error != nil)
        #expect(store.session == nil)
        #expect(try Data(contentsOf: blocker) == Data("not a directory".utf8))
    }

    @Test("a persistent page whose profile id is malformed reports it and leaves the file alone")
    func malformedProfileIsReported() throws {
        let root = try Self.stateRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("browser-profile")
        try Data("not-a-uuid".utf8).write(to: file)
        let store = LinuxBrowserStore()

        #expect(store.failure(persistent: true, stateRoot: root) == BrowserProfile.Failure.malformed(file.path).description)
        #expect(store.session == nil)
        #expect(try Data(contentsOf: file) == Data("not-a-uuid".utf8))
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("browser").path))
    }
}
