import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

struct LinuxProcCmdlineTests {
    @Test func preservesMiddleEmptyArgumentsForRestore() throws {
        let argv = try #require(CommandRestore.parseProcCmdline(Data("a\0\0b\0".utf8)))
        #expect(argv == ["a", "", "b"])
        #expect(CommandRestore.shouldRestore(argv: argv, denylist: []))
        #expect(CommandRestore.shellQuotedLine(argv) == "'a' '' 'b'")
    }

    @Test func retitledProcessPaddingIsNotReplayedAsArguments() throws {
        var bytes = Data("tool".utf8)
        bytes.append(Data(repeating: 0, count: 120))
        let argv = try #require(CommandRestore.parseProcCmdline(bytes))
        #expect(argv == ["tool"])
        #expect(CommandRestore.shellQuotedLine(argv) == "'tool'")
    }

    /// Documented limitation: the bytes cannot tell a real trailing '' from padding.
    @Test func singleTrailingEmptyArgumentIsDropped() {
        #expect(CommandRestore.parseProcCmdline(Data("tool\0\0".utf8)) == ["tool"])
        #expect(CommandRestore.parseProcCmdline(Data("tool\0\0tail\0\0".utf8)) == ["tool", "", "tail"])
    }

    @Test func allNulInputIsNotRestorable() {
        #expect(CommandRestore.parseProcCmdline(Data()) == nil)
        #expect(CommandRestore.parseProcCmdline(Data([0])) == nil)
        #expect(CommandRestore.parseProcCmdline(Data(repeating: 0, count: 64)) == nil)
    }

    @Test func removesTerminator() {
        #expect(CommandRestore.parseProcCmdline(Data("tool\0".utf8)) == ["tool"])
        #expect(CommandRestore.parseProcCmdline(Data("tool\0tail".utf8)) == ["tool", "tail"])
    }

    @Test func emptyExecutableCannotBecomeAnArgumentExecutable() throws {
        let argv = try #require(CommandRestore.parseProcCmdline(Data("\0tool\0".utf8)))
        #expect(argv == ["", "tool"])
        #expect(!CommandRestore.shouldRestore(argv: argv, denylist: []))
    }
}
