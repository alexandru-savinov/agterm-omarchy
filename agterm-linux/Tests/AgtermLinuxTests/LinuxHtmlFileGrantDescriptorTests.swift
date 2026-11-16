import Dispatch
import Foundation
import Glibc
import Testing
@testable import AgtermLinux

/// A grant `root` whose files each say where they live, and an `outside` tree mirroring its layout.
private final class SwapTree {
    let base: String
    var root: String { base + "/root" }
    var outside: String { base + "/outside" }

    init() throws {
        let raw = FileManager.default.temporaryDirectory.appendingPathComponent("agterm-grant-swap-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: raw, withIntermediateDirectories: true)
        base = raw.resolvingSymlinksInPath().path
        let fm = FileManager.default
        for dir in ["root/assets/css", "outside/assets/css"] {
            try fm.createDirectory(atPath: "\(base)/\(dir)", withIntermediateDirectories: true)
        }
        for file in ["index.html", "assets/css/site.css"] {
            #expect(fm.createFile(atPath: "\(root)/\(file)", contents: Data("inside \(file)".utf8)))
            #expect(fm.createFile(atPath: "\(outside)/\(file)", contents: Data("outside \(file)".utf8)))
        }
        try fm.createSymbolicLink(atPath: "\(root)/link-in", withDestinationPath: "\(root)/assets/css/site.css")
        try fm.createSymbolicLink(atPath: "\(base)/root-link", withDestinationPath: root)
    }

    deinit { try? FileManager.default.removeItem(atPath: base) }

    func checked(_ uriPath: String, grant: String? = nil) throws -> LinuxHtmlFileGrant.Resource {
        try #require(LinuxHtmlFileGrant.resource(for: uriPath, grantRoot: grant ?? root))
    }

    /// Replaces the entry at `relative` after `resource` accepted it and before the open.
    func swap(_ relative: String, _ make: (String) throws -> Void) throws {
        try FileManager.default.removeItem(atPath: "\(root)/\(relative)")
        try make("\(root)/\(relative)")
    }

    func open(_ resource: LinuxHtmlFileGrant.Resource, _ resolution: LinuxHtmlFileGrant.Resolution,
              grant: String? = nil) -> Int32? {
        LinuxHtmlFileGrant.openDescriptor(resource, grantRoot: grant ?? root, resolution: resolution)
    }
}

private func contents(_ descriptor: Int32?) -> String? {
    guard let descriptor else { return nil }
    defer { close(descriptor) }
    var bytes = [UInt8](repeating: 0, count: 256)
    let count = read(descriptor, &bytes, bytes.count)
    return count < 0 ? nil : String(decoding: bytes.prefix(count), as: UTF8.self)
}

/// Written by one background open and read only after its `opened` signal.
private final class DescriptorBox: @unchecked Sendable {
    let opened = DispatchSemaphore(value: 0)
    var descriptor: Int32?
}

private let resolutions: [LinuxHtmlFileGrant.Resolution] =
    LinuxHtmlFileGrant.beneathResolutionAvailable ? [.beneath, .components] : [.components]

@Suite("HTML file grants serve the checked file from one descriptor opened beneath the root")
struct LinuxHtmlFileGrantDescriptorTests {
    @Test(arguments: resolutions)
    func aCheckedFileIsServedFromItsDescriptor(_ resolution: LinuxHtmlFileGrant.Resolution) throws {
        let tree = try SwapTree()
        let index = try tree.checked("/index.html")
        let linkIn = try tree.checked("/link-in")
        #expect(contents(tree.open(index, resolution)) == "inside index.html")
        #expect(contents(tree.open(linkIn, resolution)) == "inside assets/css/site.css")
        let linked = try tree.checked("/assets/css/site.css", grant: "\(tree.base)/root-link")
        #expect(contents(tree.open(linked, resolution, grant: "\(tree.base)/root-link")) == "inside assets/css/site.css")
    }

    @Test(arguments: resolutions)
    func aFileSwappedForASymlinkIsNotFollowed(_ resolution: LinuxHtmlFileGrant.Resolution) throws {
        let tree = try SwapTree()
        for target in ["\(tree.outside)/index.html", "../outside/index.html", "\(tree.root)/assets/css/site.css"] {
            let resource = try tree.checked("/index.html")
            try tree.swap("index.html") { try FileManager.default.createSymbolicLink(atPath: $0, withDestinationPath: target) }
            #expect(contents(tree.open(resource, resolution)) == nil, "\(target)")
            try tree.swap("index.html") { #expect(FileManager.default.createFile(atPath: $0, contents: Data("inside index.html".utf8))) }
        }
    }

    @Test(arguments: resolutions)
    func aParentSwappedForASymlinkIsNotFollowed(_ resolution: LinuxHtmlFileGrant.Resolution) throws {
        let tree = try SwapTree()
        let resource = try tree.checked("/assets/css/site.css")
        try tree.swap("assets") { try FileManager.default.createSymbolicLink(atPath: $0, withDestinationPath: "\(tree.outside)/assets") }
        #expect(contents(tree.open(resource, resolution)) == nil)
    }

    @Test(arguments: resolutions)
    func aFileSwappedForADirectoryIsRefused(_ resolution: LinuxHtmlFileGrant.Resolution) throws {
        let tree = try SwapTree()
        let resource = try tree.checked("/index.html")
        try tree.swap("index.html") { try FileManager.default.createDirectory(atPath: $0, withIntermediateDirectories: false) }
        #expect(contents(tree.open(resource, resolution)) == nil)
    }

    @Test(arguments: resolutions)
    func aFileSwappedForAFifoIsRefusedWithoutBlocking(_ resolution: LinuxHtmlFileGrant.Resolution) throws {
        let tree = try SwapTree()
        let resource = try tree.checked("/index.html")
        try tree.swap("index.html") { #expect(mkfifo($0, 0o600) == 0) }
        let root = tree.root
        let result = DescriptorBox()
        DispatchQueue.global().async {
            result.descriptor = LinuxHtmlFileGrant.openDescriptor(resource, grantRoot: root, resolution: resolution)
            result.opened.signal()
        }
        if result.opened.wait(timeout: .now() + 5) == .timedOut {
            Issue.record("opening a swapped-in FIFO blocked waiting for a writer")
            repeat {
                let writer = open("\(root)/index.html", O_WRONLY | O_NONBLOCK | O_CLOEXEC)
                if writer >= 0 { close(writer) }
            } while result.opened.wait(timeout: .now() + 0.1) == .timedOut
        }
        #expect(contents(result.descriptor) == nil)
    }

    @Test(arguments: resolutions)
    func anOpenDescriptorKeepsTheCheckedFileAfterASwap(_ resolution: LinuxHtmlFileGrant.Resolution) throws {
        let tree = try SwapTree()
        let resource = try tree.checked("/index.html")
        let descriptor = tree.open(resource, resolution)
        try tree.swap("index.html") {
            try FileManager.default.createSymbolicLink(atPath: $0, withDestinationPath: "\(tree.outside)/index.html")
        }
        #expect(contents(descriptor) == "inside index.html")
    }

    @Test func theDefaultResolutionRefusesASwap() throws {
        let tree = try SwapTree()
        let resource = try tree.checked("/index.html")
        try tree.swap("index.html") {
            try FileManager.default.createSymbolicLink(atPath: $0, withDestinationPath: "\(tree.outside)/index.html")
        }
        #expect(contents(LinuxHtmlFileGrant.openDescriptor(resource, grantRoot: tree.root)) == nil)
    }
}
