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

    /// A retitled process (`npm run dev` + NUL padding) is NOT restorable: replaying it would type the
    /// whole title as one quoted word. The parser contract from #209 is unchanged underneath.
    @Test func retitledMultiWordTitleIsNotRestorable() {
        var bytes = Data("npm run dev".utf8)
        bytes.append(Data(repeating: 0, count: 64))
        #expect(CommandRestore.parseProcCmdline(bytes) == ["npm run dev"])
        #expect(CommandRestore.restorableProcCmdline(bytes) == nil)
        #expect(CommandRestore.restorableProcCmdline(Data("npm run dev\0\0".utf8)) == nil)
    }

    @Test func restorableCaptureKeepsOrdinaryArgv() throws {
        #expect(CommandRestore.restorableProcCmdline(Data("tool\0".utf8)) == ["tool"])
        #expect(CommandRestore.restorableProcCmdline(Data("zsh\0-c\0echo hi\0".utf8)) == ["zsh", "-c", "echo hi"])
        #expect(CommandRestore.restorableProcCmdline(Data()) == nil)
        // a padded single WORD cannot be told from a real one-word argv: documented limitation, restorable.
        var padded = Data("claude".utf8)
        padded.append(Data(repeating: 0, count: 64))
        #expect(CommandRestore.restorableProcCmdline(padded) == ["claude"])
        // a real argv[0] with a space ends in exactly one NUL — stays restorable as one word.
        let spaced = try #require(CommandRestore.restorableProcCmdline(Data("a b\0".utf8)))
        #expect(spaced == ["a b"])
        #expect(CommandRestore.shouldRestore(argv: spaced, denylist: []))
        #expect(CommandRestore.shellQuotedLine(spaced) == "'a b'")
    }

    @Test func emptyExecutableCannotBecomeAnArgumentExecutable() throws {
        let argv = try #require(CommandRestore.parseProcCmdline(Data("\0tool\0".utf8)))
        #expect(argv == ["", "tool"])
        #expect(!CommandRestore.shouldRestore(argv: argv, denylist: []))
    }
}
