import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

struct LinuxProcCmdlineTests {
    @Test func preservesEmptyArgumentsForRestore() throws {
        let argv = try #require(CommandRestore.parseProcCmdline(Data("tool\0\0tail\0\0".utf8)))
        #expect(argv == ["tool", "", "tail", ""])
        #expect(CommandRestore.shouldRestore(argv: argv, denylist: []))
        #expect(CommandRestore.shellQuotedLine(argv) == "'tool' '' 'tail' ''")
    }

    @Test func removesOnlyOneTerminator() {
        #expect(CommandRestore.parseProcCmdline(Data()) == nil)
        #expect(CommandRestore.parseProcCmdline(Data("tool\0".utf8)) == ["tool"])
        #expect(CommandRestore.parseProcCmdline(Data("tool\0\0\0".utf8)) == ["tool", "", ""])
        #expect(CommandRestore.parseProcCmdline(Data("tool\0tail".utf8)) == ["tool", "tail"])
    }

    @Test func emptyExecutableCannotBecomeAnArgumentExecutable() throws {
        let argv = try #require(CommandRestore.parseProcCmdline(Data("\0tool\0".utf8)))
        #expect(argv == ["", "tool"])
        #expect(!CommandRestore.shouldRestore(argv: argv, denylist: []))
    }
}
